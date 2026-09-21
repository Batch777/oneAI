from __future__ import annotations
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import FileResponse, JSONResponse
from . import auth
from ..config import Config
from ..tasks import Tasks, digest
from ..indexer import Index
from ..vault import select_lines

STATIC=Path(__file__).parent/'static'
COOKIE='oneai_session'


def create_app(cfg=None,origin=None,dev=False):
    cfg=cfg or Config.load(); cfg.ensure_dirs()
    origin=origin or os.environ.get('ONEAI_APP_ORIGIN','https://47.82.117.21')
    parsed=urlsplit(origin)
    if parsed.scheme!='https' and not (dev and parsed.hostname in ('127.0.0.1','localhost','testserver')):
        raise ValueError('HTTPS origin required')
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.cfg=cfg

    @app.middleware('http')
    async def guard(request,call_next):
        if request.headers.get('host')!=parsed.netloc:
            return JSONResponse({'detail':'invalid_host'},400)
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('origin')!=origin:
                return JSONResponse({'detail':'origin_rejected'},403)
            if 'application/json' not in request.headers.get('content-type',''):
                return JSONResponse({'detail':'json_required'},415)
            try: size=int(request.headers.get('content-length','0'))
            except ValueError: size=200001
            if size>200000: return JSONResponse({'detail':'request_too_large'},413)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
        return response

    def authenticated(request:Request):
        value=auth.session(cfg,request.cookies.get(COOKIE))
        if not value: raise HTTPException(401,'login_required')
        if request.method not in ('GET','HEAD') and not secrets.compare_digest(value['csrf'],request.headers.get('x-oneai-csrf','')):
            raise HTTPException(403,'csrf_rejected')
        return value

    async def body(request):
        parts=[]; size=0
        async for part in request.stream():
            size+=len(part)
            if size>200000: raise HTTPException(413,'request_too_large')
            parts.append(part)
        raw=b''.join(parts)
        if len(raw)>200000: raise HTTPException(413,'request_too_large')
        try: data=json.loads(raw)
        except ValueError: raise HTTPException(400,'invalid_json')
        if not isinstance(data,dict): raise HTTPException(400,'object_required')
        return data

    @app.post('/api/login')
    async def login(request:Request,response:Response):
        data=await body(request)
        name=data.get('name','我的设备')
        if not isinstance(name,str): raise HTTPException(400,'invalid_name')
        try: token,csrf,sid=auth.login(cfg,data.get('code',''),name,request.client.host)
        except ValueError as error: raise HTTPException(429 if str(error)=='too_many_attempts' else 401,str(error))
        response.set_cookie(COOKIE,token,max_age=30*86400,secure=not dev,httponly=True,samesite='strict',path='/')
        return {'csrf':csrf,'device_id':sid}

    @app.get('/api/session')
    def session(user=Depends(authenticated)):
        return {'csrf':user['csrf'],'device_id':user['id'],'name':user['name']}

    @app.post('/api/logout')
    def logout(response:Response,user=Depends(authenticated)):
        with auth.database(cfg) as db,db: db.execute('DELETE FROM sessions WHERE hash=?',(user['hash'],))
        response.delete_cookie(COOKIE,path='/',secure=not dev,httponly=True,samesite='strict')
        return {'ok':True}

    @app.get('/api/devices')
    def devices(user=Depends(authenticated)):
        with auth.database(cfg) as db:
            return {'devices':[dict(r) for r in db.execute('SELECT id,name,created,expires FROM sessions WHERE expires>? ORDER BY created',(time.time(),))]}

    @app.post('/api/pair')
    def pair(user=Depends(authenticated)):
        return {'code':auth.pair(cfg),'expires_in':600}

    @app.post('/api/devices/revoke')
    async def revoke(request:Request,user=Depends(authenticated)):
        data=await body(request)
        if not isinstance(data.get('id'),str): raise HTTPException(400,'invalid_device')
        with auth.database(cfg) as db,db: db.execute('DELETE FROM sessions WHERE id=?',(data['id'],))
        return {'ok':True}

    @app.get('/api/tasks')
    def tasks(q:str='',status:str='',offset:int=0,user=Depends(authenticated)):
        if len(q)>200 or offset<0: raise HTTPException(400,'invalid_query')
        store=Tasks(cfg.state_path/'tasks.sqlite')
        try:
            where=[]; values=[]
            if status:
                if status not in ('pending','needs_review','reviewed','completed'): raise HTTPException(400,'invalid_status')
                where.append('status=?'); values.append(status)
            if q: where.append('(instr(lower(title),lower(?))>0 OR instr(lower(input),lower(?))>0)'); values.extend([q,q])
            clause=' WHERE '+' AND '.join(where) if where else ''
            total=store.db.execute('SELECT count(*) FROM tasks'+clause,values).fetchone()[0]
            rows=store.db.execute('SELECT id,title,status,revision,updated FROM tasks'+clause+' ORDER BY updated DESC,id DESC LIMIT 50 OFFSET ?',values+[offset]).fetchall()
            return {'items':[dict(r) for r in rows],'total':total}
        finally: store.db.close()

    @app.get('/api/tasks/{task_id}')
    def task(task_id:str,user=Depends(authenticated)):
        store=Tasks(cfg.state_path/'tasks.sqlite')
        try:
            row=store.get(task_id)
            row['sources']=json.loads(row['sources']); row['rules']=json.loads(row['rules'])
            return row
        except ValueError: raise HTTPException(404,'task_not_found')
        finally: store.db.close()

    @app.post('/api/commands')
    async def command(request:Request,user=Depends(authenticated)):
        data=await body(request)
        if data.get('action') not in ('create','revise','approve','complete'): raise HTTPException(400,'unsupported_action')
        if not isinstance(data.get('id'),str) or len(data['id'])>128: raise HTTPException(400,'invalid_command_id')
        if data['action']=='create' and (not isinstance(data.get('title'),str) or len(data['title'])>300): raise HTTPException(400,'invalid_title')
        if data['action']!='create' and (not isinstance(data.get('task_id'),str) or not re.fullmatch('[a-f0-9]{24}',data['task_id'])): raise HTTPException(400,'invalid_task_id')
        if 'body' in data and (not isinstance(data['body'],str) or len(data['body'])>50000): raise HTTPException(400,'invalid_body')
        store=Tasks(cfg.state_path/'tasks.sqlite')
        try:
            store.command(data)
            return {'accepted':True,'task_id':digest('phone:'+data['id'])[:24] if data['action']=='create' else data['task_id']}
        except (ValueError,TypeError) as error: raise HTTPException(409,str(error))
        finally: store.db.close()

    @app.get('/api/status')
    def status(user=Depends(authenticated)):
        store=Tasks(cfg.state_path/'tasks.sqlite')
        try: counts={r[0]:r[1] for r in store.db.execute('SELECT status,count(*) FROM tasks GROUP BY status')}
        finally: store.db.close()
        worker=None
        try: worker=json.loads((cfg.state_path/'worker-status.json').read_text())
        except (FileNotFoundError,ValueError): pass
        mail=None
        try: mail=json.loads((cfg.state_path/'outlook-status.json').read_text())
        except (FileNotFoundError,ValueError): pass
        return {'counts':counts,'worker':worker,'mail':mail,'time':time.time()}

    @app.get('/api/search')
    def search(q:str,user=Depends(authenticated)):
        if not q.strip() or len(q)>200: raise HTTPException(400,'invalid_query')
        idx=Index(cfg.index_db)
        try: return {'items':[{'citation':r.citation,'text':r.text,'heading':r.heading} for r in idx.search(q,k=20)]}
        finally: idx.close()

    @app.get('/api/source')
    def source(citation:str,user=Depends(authenticated)):
        match=re.fullmatch(r'(.+)@([a-f0-9]{64})#L([0-9]+)-L?([0-9]+)',citation)
        if not match: raise HTTPException(400,'invalid_citation')
        path,version,start,end=match.groups(); idx=Index(cfg.index_db)
        try:
            raw=idx.read_version(cfg.vault_path,path,version)
            return {'citation':citation,'text':select_lines(raw,f'{start}-{end}')}
        except (ValueError,FileNotFoundError,KeyError): raise HTTPException(404,'source_not_found')
        finally: idx.close()

    @app.get('/')
    def home(): return FileResponse(STATIC/'index.html')

    @app.get('/{name}')
    def static(name:str):
        if name not in ('app.js','app.css','sw.js','manifest.webmanifest','icon.svg','icon-192.png','icon-512.png','apple-touch-icon.png'): raise HTTPException(404)
        return FileResponse(STATIC/name)

    return app


def app_factory():
    return create_app(dev=os.environ.get('ONEAI_APP_DEV')=='1')
