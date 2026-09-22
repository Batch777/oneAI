"""Read-only Outlook presentation: semantic HTML, bounded lazy Graph attachments."""
from __future__ import annotations
import fcntl
import base64
import io
import threading
import hashlib
import json
import re
import sqlite3
import urllib.request
from contextlib import contextmanager
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit
from .vault import atomic_write
from connectors.outlook.connector import GraphAuth, GRAPH, NoRedirect, validate_url

MAX_ATTACHMENT=25*1024*1024
TAGS=set('p br strong b em i u ul ol li code pre blockquote h1 h2 h3 h4 h5 h6 table thead tbody tfoot tr td th div span hr a'.split())
SKIP=set('script style head iframe object embed svg math form template noscript'.split())
VOID={'br','hr','img','input','meta','link','embed'}


def safe_link(value):
    if not isinstance(value,str) or len(value)>8192 or any(ord(c)<32 for c in value):return None
    try:
        p=urlsplit(value)
        if p.scheme.lower() in ('http','https') and p.hostname and not p.username and not p.password:
            p.port
            return value
        if p.scheme.lower()=='mailto' and p.path and not any(c in value.lower() for c in ('%0a','%0d')):return value
    except ValueError:pass
    return None


class ReadableHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes=[];self.stack=[('',self.nodes)];self.suppressed=[];self.count=0
    def handle_starttag(self,tag,attrs):
        if self.suppressed:
            if tag not in VOID:self.suppressed.append(tag)
            return
        if tag in SKIP:
            if tag not in VOID:self.suppressed.append(tag)
            return
        if self.count>=10000 or len(self.stack)>40:return
        self.count+=1;attrs=dict(attrs)
        if tag=='img':
            self.stack[-1][1].append({'tag':'image','alt':attrs.get('alt','图片')[:500],
                'cid':attrs.get('src','')[4:][:500] if attrs.get('src','').lower().startswith('cid:') else None,
                'remote_src':safe_link(attrs.get('src','')) if attrs.get('src','').lower().startswith('https://') else None})
            return
        if tag not in TAGS:return
        node={'tag':tag,'children':[]}
        if tag=='a':node['href']=safe_link(attrs.get('href',''))
        if tag in ('td','th'):
            for key in ('colspan','rowspan'):
                if attrs.get(key,'').isdigit():node[key]=min(20,max(1,int(attrs[key])))
        self.stack[-1][1].append(node)
        if tag not in VOID:self.stack.append((tag,node['children']))
    def handle_endtag(self,tag):
        if self.suppressed:
            if tag in self.suppressed:
                self.suppressed=self.suppressed[:len(self.suppressed)-1-self.suppressed[::-1].index(tag)]
            return
        for n in range(len(self.stack)-1,0,-1):
            if self.stack[n][0]==tag:self.stack=self.stack[:n];break
    def handle_data(self,data):
        if not self.suppressed and self.count<10000:
            self.count+=1;self.stack[-1][1].append({'text':data})


def readable(body):
    content=str(body.get('content',''))
    if len(content)>1_000_000:raise ValueError('mail_too_large')
    if str(body.get('contentType','')).lower()!='html':return [{'tag':'pre','children':[{'text':content}]}]
    parser=ReadableHTML();parser.feed(content);parser.close();return parser.nodes


def message_for_task(cfg,task):
    if not task['origin'].startswith('mail:'):raise ValueError('not_mail')
    path=cfg.state_path/'outlook.sqlite'
    if not path.exists():raise ValueError('mail_unavailable')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT w.message_id,m.deleted FROM work w JOIN messages m ON m.id=w.message_id WHERE w.id=?',(task['origin'][5:],)).fetchone()
    if not row or row[1]:raise ValueError('mail_unavailable')
    return row[0]


@contextmanager
def graph_session(cfg):
    # Share the token-cache lock with delta sync; never wait behind a long sync.
    with (cfg.state_path/'outlook.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('mail_sync_busy') from None
        token=GraphAuth(cfg).token()
    # The lock protects token-cache mutations, not long mail/attachment transfers.
    class AccessToken:
        def token(self):return token
    yield AccessToken()


def graph_get(auth,url,limit=4*1024*1024,binary=False):
    validate_url(url)
    req=urllib.request.Request(url,headers={'Authorization':'Bearer '+auth.token(),
        'Prefer':'IdType="ImmutableId", outlook.body-content-type="html"'})
    with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as response:
        data=response.read(limit+1)
    if len(data)>limit:raise ValueError('mail_too_large')
    return data if binary else json.loads(data)


def _cache_path(cfg,mid):
    return cfg.state_path/'mail-display'/ (hashlib.sha256(mid.encode()).hexdigest()+'.json')


def display(cfg,task,include_attachments=True):
    mid=message_for_task(cfg,task);path=_cache_path(cfg,mid)
    # The event identifies the synchronized message version; another event invalidates cache.
    if path.exists():
        cached=json.loads(path.read_text())
        if cached.get('version')==2 and cached.get('event')==task['origin']:
            view=cached['view']
            if not include_attachments or view.get('attachments_loaded',True):return view
            return _with_attachments(cfg,task,mid,path,view)
    base=GRAPH+'/me/messages/'+quote(mid,safe='')
    with graph_session(cfg) as auth:
        message=graph_get(auth,base+'?$select=id,subject,from,toRecipients,ccRecipients,replyTo,receivedDateTime,body,webLink,hasAttachments')
    body=message.get('body') or {'contentType':'text','content':task['input']}
    view={'subject':message.get('subject',task['title']),'from':message.get('from'),
          'to':message.get('toRecipients',[]),'cc':message.get('ccRecipients',[]),'reply_to':message.get('replyTo',[]),
          'received_at':message.get('receivedDateTime'),'web_link':safe_link(message.get('webLink')),
          'body_type':body.get('contentType','text'),'raw':body.get('content',''),
          'nodes':readable(body),'attachments':[],'attachments_loaded':False,'remote_images':'blocked'}
    atomic_write(path,json.dumps({'version':2,'event':task['origin'],'view':view},ensure_ascii=False))
    return _with_attachments(cfg,task,mid,path,view) if include_attachments else view


def _with_attachments(cfg,task,mid,path,view):
    base=GRAPH+'/me/messages/'+quote(mid,safe='')
    with graph_session(cfg) as auth:
        attachments=[];url=base+'/attachments?$select=id,name,contentType,size,isInline'
        # Do not use hasAttachments as the sole gate: inline-only messages report false.
        for _ in range(5):
            page=graph_get(auth,url)
            attachments.extend(page.get('value',[]));url=page.get('@odata.nextLink')
            if not url:break
        else:raise ValueError('too_many_attachments')
    items=[]
    for a in attachments:
        kind=a.get('@odata.type','')
        items.append({'id':a['id'],'name':a.get('name','附件'),'content_type':a.get('contentType','application/octet-stream'),
            'size':a.get('size',0),'inline':bool(a.get('isInline')),'kind':kind,
            'downloadable':kind in ('#microsoft.graph.fileAttachment','#microsoft.graph.itemAttachment') and a.get('size',0)<=MAX_ATTACHMENT})
    view={**view,'attachments':items,'attachments_loaded':True}
    atomic_write(path,json.dumps({'version':2,'event':task['origin'],'view':view},ensure_ascii=False))
    return view


def attachment(cfg,task,attachment_id):
    mid=message_for_task(cfg,task);view=display(cfg,task)
    item=next((a for a in view['attachments'] if a['id']==attachment_id),None)
    if not item or not item['downloadable']:raise ValueError('attachment_unavailable')
    with graph_session(cfg) as auth:
        data=graph_get(auth,GRAPH+'/me/messages/'+quote(mid,safe='')+'/attachments/'+quote(attachment_id,safe='')+'/$value',MAX_ATTACHMENT,True)
    name=re.sub(r'[\x00-\x1f\x7f/\\]','_',item['name'])[:180] or 'attachment'
    if item['kind']=='#microsoft.graph.itemAttachment' and not name.lower().endswith('.eml'):name+='.eml'
    return data,name,item['content_type']


_thumbnail_lock=threading.Lock()

def thumbnail(cfg,task,attachment_id):
    # Serialise decode on the small relay; never retain the original attachment.
    mid=message_for_task(cfg,task)
    view=display(cfg,task)
    if not any(a['id']==attachment_id and a['downloadable'] for a in view['attachments']):
        raise ValueError('attachment_unavailable')
    key=hashlib.sha256((mid+'\0'+task['origin']+'\0'+attachment_id).encode()).hexdigest()
    folder=cfg.state_path/'mail-thumbnails';path=folder/(key+'.json')
    if path.exists():return base64.b64decode(path.read_text())
    if not _thumbnail_lock.acquire(timeout=20):raise RuntimeError('thumbnail_busy')
    try:
        if path.exists():return base64.b64decode(path.read_text())
        data,_,_=attachment(cfg,task,attachment_id)
        from PIL import Image, ImageOps, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(data),formats=['PNG','JPEG','GIF','WEBP']) as original:
                if original.width*original.height>20_000_000:raise ValueError('thumbnail_unavailable')
                original.seek(0)
                original.thumbnail((480,480))
                frame=ImageOps.exif_transpose(original).convert('RGBA')
                canvas=Image.new('RGB',frame.size,'white');canvas.paste(frame,mask=frame.getchannel('A'))
                buffer=io.BytesIO();canvas.save(buffer,'JPEG',quality=65,optimize=True)
                result=buffer.getvalue()
        except (UnidentifiedImageError,Image.DecompressionBombError,OSError):
            raise ValueError('thumbnail_unavailable') from None
        # Bound persistent previews to 100 MiB. Originals are never stored here.
        files=sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime) if folder.exists() else []
        total=sum(p.stat().st_size for p in files)
        for old in files:
            if total<100*1024*1024:break
            total-=old.stat().st_size;old.unlink(missing_ok=True)
        atomic_write(path,base64.b64encode(result).decode('ascii'))
        return result
    finally:_thumbnail_lock.release()
