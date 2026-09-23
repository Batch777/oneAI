"""Authenticated control plane. Host credentials cannot act as user credentials."""
import json
from fastapi import Depends,HTTPException,Request
from .store import Store
from ..sessions.store import Store as Hosts


def mount(app,cfg,authenticated,body):
    store=Store(cfg.state_path/'plugins.sqlite');hosts=Hosts(cfg.state_path/'sessions.sqlite')
    def executor(request:Request):
        header=request.headers.get('authorization','')
        try:
            if not header.startswith('Bearer '):raise ValueError('host_unauthorized')
            hid=hosts.authenticate(header[7:])
            allowed=next(h for h in hosts.hosts() if h['id']==hid)
            if allowed['capabilities'].get('plugins') is not True:raise ValueError('plugin_host_not_authorized')
            return hid
        except (ValueError,StopIteration) as error:raise HTTPException(403,str(error))
    def fail(error):return HTTPException(409,str(error))
    @app.get('/api/plugins')
    def listing(user=Depends(authenticated)):
        by_id={h['id']:h for h in hosts.hosts()}
        items=store.list()
        for p in items:
            h=by_id.get(p['host_id'],{});p['host_name']=h.get('name','Linux');p['workspaces']=h.get('capabilities',{}).get('workspaces',[])
        return {'items':items,'runs':store.runs()}
    @app.post('/api/plugins/{key}/state')
    async def state(key:str,request:Request,user=Depends(authenticated)):
        try:
            d=await body(request);store.toggle(key,d.get('revision'),d.get('enabled'));return {'ok':True}
        except (ValueError,TypeError,KeyError) as e:raise fail(e)
    @app.post('/api/plugin-actions')
    async def invoke(request:Request,user=Depends(authenticated)):
        try:return store.invoke(await body(request),{h['id']:h['capabilities']['workspaces'] for h in hosts.hosts()})
        except (ValueError,TypeError,KeyError) as e:raise fail(e)
    @app.post('/api/agent-host/plugins/inventory')
    async def inventory(request:Request,hid=Depends(executor)):
        try:store.publish(hid,(await body(request)).get('items'));return {'ok':True}
        except (ValueError,TypeError,KeyError) as e:raise fail(e)
    @app.post('/api/agent-host/plugins/poll')
    def poll(hid=Depends(executor)):return {'invocation':store.claim(hid)}
    @app.post('/api/agent-host/plugins/result')
    async def result(request:Request,hid=Depends(executor)):
        try:store.result(hid,await body(request));return {'ok':True}
        except (ValueError,TypeError,KeyError) as e:raise fail(e)
