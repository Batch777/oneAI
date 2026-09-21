import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient
from oneai.config import Config
from oneai.sessions.store import Store
from oneai.sessions.adapters import JsonLines, Pi
from oneai.web.app import create_app
from oneai.web import auth

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'sessions.sqlite')
    h=s.enroll('Mac',['project'],['codex','pi'])
    return s,h

def create(s,h):
    return s.command({'id':'create','action':'create','host_id':h['id'],'provider':'codex','workspace':'project','title':'Session'})['session_id']

def report(s,h,sid,state,eid='event',cid=None):
    s.report(h['id'],{'id':eid,'session_id':sid,'kind':'status','payload':{'state':state,'command_id':cid}})

def test_two_devices_retry_conflict_and_stop(store):
    s,h=store; sid=create(s,h)
    c=s.claim(h['id']); assert c['session_id']==sid
    assert s.claim(h['id']) is None  # no redelivery even if response was lost
    report(s,h,sid,'idle',cid='create')
    rev=s.sessions()[0]['revision']
    command={'id':'first','session_id':sid,'action':'prompt','revision':rev,'text':'hello'}
    assert s.command(command)['state']=='queued'
    assert s.command(command)['state']=='queued'
    with pytest.raises(ValueError,match='command_id_conflict'):s.command({**command,'text':'different'})
    with pytest.raises(ValueError,match='stale_session'):s.command({**command,'id':'second'})
    s.command({'id':'stop','action':'interrupt','session_id':sid,'revision':s.sessions()[0]['revision']})
    assert s.claim(h['id'])['id']=='stop'
    assert s.claim(h['id']) is None  # queued prompt was cancelled

def test_concurrent_submit_has_one_winner(store):
    s,h=store;sid=create(s,h);s.claim(h['id']);report(s,h,sid,'idle')
    rev=s.sessions()[0]['revision']
    def submit(i):
        try:return s.command({'id':str(i),'action':'prompt','session_id':sid,'revision':rev,'text':'hello'})
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(r is not None for r in pool.map(submit,range(2)))==1

def test_scope_event_replay_and_expiration(store):
    s,h=store;sid=create(s,h)
    other=s.enroll('Other',['project'],['pi'])
    assert s.claim(other['id']) is None
    with pytest.raises(ValueError,match='session_owner'):report(s,other,sid,'idle')
    with pytest.raises(ValueError,match='unauthorized'):s.authenticate('wrong')
    assert s.authenticate(h['token'])==h['id']
    assert 'token_hash' not in s.hosts()[0]
    with s.db() as db:db.execute('UPDATE session_commands SET expires=0')
    assert s.claim(h['id']) is None
    assert s.sessions()[0]['state']=='unknown'
    report(s,h,sid,'idle');report(s,h,sid,'idle')
    with pytest.raises(ValueError,match='event_id_conflict'):report(s,h,sid,'running')
    events=s.replay(sid);assert len(events)==3
    assert not s.replay(sid,events[-1]['seq'])
    assert Store(s.path).replay(sid)==events

def test_credentials_do_not_cross_browser_host_boundary(tmp_path,monkeypatch):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None)
    app=create_app(cfg,origin='https://testserver')
    c=TestClient(app,base_url='https://testserver')
    c.headers['origin']='https://testserver'
    r=c.post('/api/login',json={'code':auth.pair(cfg)})
    c.headers['x-oneai-csrf']=r.json()['csrf']
    assert c.get('/api/agent/hosts').status_code==403
    monkeypatch.setenv('ONEAI_ENABLE_SESSIONS','1')
    s=Store(cfg.state_path/'sessions.sqlite');h=s.enroll('Host',['p'],['pi'])
    assert c.post('/api/agent-host/poll',json={}).status_code==401
    assert c.get('/api/agent/hosts').status_code==200
    remote=TestClient(app,base_url='https://testserver')
    remote.headers['authorization']='Bearer '+h['token']
    assert remote.post('/api/agent-host/poll',json={}).status_code==200
    assert remote.get('/api/agent/sessions').status_code==401
    assert c.post('/api/agent/commands',headers={'origin':'https://evil.test'},json={}).status_code==403

def test_jsonl_unicode_and_process_exit(tmp_path):
    script=tmp_path/'fake.py'
    script.write_text('''import sys,json
for line in sys.stdin.buffer:
 r=json.loads(line)
 print(json.dumps({'id':r['id'],'result':{'value':'a\\u2028b'}},ensure_ascii=False),flush=True)
''')
    events=[]
    rpc=JsonLines([sys.executable,str(script)],tmp_path,events.append)
    try:assert rpc.request('test')['value']=='a\u2028b'
    finally:rpc.close()
    assert rpc.process.poll() is not None

def test_pi_stop_clears_queue_before_abort():
    calls=[]
    class RPC:
        def request(self,name,**kwargs):calls.append(name)
    pi=object.__new__(Pi);pi.rpc=RPC();pi.emit=lambda *args:None
    pi.interrupt()
    assert calls==['clear_queue','abort']

def test_pi_does_not_mark_idle_before_retries_settle():
    values=[];pi=object.__new__(Pi);pi.emit=lambda *args:values.append(args)
    pi.event({'type':'agent_end','willRetry':True})
    assert values==[]
    pi.event({'type':'agent_settled'})
    assert values==[('status',{'state':'idle'})]

def test_lost_dispatch_is_unknown_and_never_redelivered(store):
    s,h=store;sid=create(s,h);s.claim(h['id'])
    with s.db() as db:db.execute('UPDATE session_commands SET dispatched=0')
    assert s.sessions()[0]['state']=='unknown'
    assert s.claim(h['id']) is None

def test_close_resume_and_pending_event_order(store):
    s,h=store;sid=create(s,h);s.claim(h['id']);report(s,h,sid,'idle')
    s.command({'id':'close','action':'close','session_id':sid,'revision':s.sessions()[0]['revision']})
    # An old idle event cannot clear the pending close.
    report(s,h,sid,'idle','late')
    assert s.sessions()[0]['state']=='closing'
    assert s.claim(h['id'])['id']=='close'
    report(s,h,sid,'closed','closed',cid='close')
    s.command({'id':'resume','action':'resume','session_id':sid,'revision':s.sessions()[0]['revision']})
    assert s.claim(h['id'])['payload']['action']=='resume'


def test_outbox_survives_relay_failure_without_repeating_runtime(tmp_path,monkeypatch):
    from oneai.sessions.host import Host
    calls=[]
    class Adapter:
        def __init__(self,binary,cwd,directory,emit,remote_id):self.remote_id='runtime-1';self.emit=emit
        def prompt(self,text,on_sent=None):on_sent();calls.append(text);self.emit('text',{'text':'response'});self.emit('status',{'state':'idle'})
        def close(self):pass
    monkeypatch.setitem(__import__('oneai.sessions.host',fromlist=['ADAPTERS']).ADAPTERS,'codex',Adapter)
    config={'providers':['codex'],'workspaces':{'p':str(tmp_path)},'binaries':{'codex':'unused'}}
    host=Host(config,tmp_path/'host')
    command={'id':'c','session_id':'s','provider':'codex','workspace':'p','payload':{'action':'prompt','text':'once'}}
    queue=[command,command,None]
    delivered=[]
    def request(route,data):
        if route=='poll':return {'command':queue.pop(0)}
        delivered.extend(data['events']);return {}
    host.request=request
    host.step();host.futures['s'].result(5)
    host.step()
    assert calls==['once']
    host.emit('s','text',{'text':'retained'})
    def offline(*args):raise ConnectionError('offline')
    host.request=offline
    with pytest.raises(ConnectionError):host.flush()
    host.close()
    restored=Host(config,tmp_path/'host');restored.request=request
    restored.flush();restored.close()
    assert any(e['payload'].get('text')=='retained' for e in delivered)
    assert calls==['once']

def test_existing_desktop_binding_can_never_become_a_second_writer(store):
    s,h=store
    sid=s.bind(h['id'],'project','Existing desktop','existing-thread')['session_id']
    assert s.sessions()[0]['mode']=='observe'
    for action in ('prompt','resume','interrupt','close'):
        with pytest.raises(ValueError,match='read_only'):
            s.command({'id':action,'action':action,'session_id':sid,'revision':0,'text':'unsafe second writer'})
    with pytest.raises(ValueError,match='read_only'):
        report(s,h,sid,'idle')
    assert s.claim(h['id']) is None

def test_observer_uses_read_apis_and_never_mirrors_reasoning(tmp_path,monkeypatch):
    from oneai.sessions.observe import Observer
    methods=[];emitted=[]
    class RPC:
        def __init__(self,*args):pass
        def send(self,value):pass
        def close(self):pass
        def request(self,method,params):
            methods.append(method)
            if method=='account/read':return {'account':{'type':'chatgpt'}}
            if method=='thread/read':return {'thread':{'cwd':str(tmp_path)}}
            if method=='thread/items/list':return {'data':[
                {'turnId':'t','item':{'id':'thought','type':'reasoning','content':['private internal reasoning']}},
                {'turnId':'t','item':{'id':'a','type':'agentMessage','text':'Visible reply'}},
                {'turnId':'t','item':{'id':'u','type':'userMessage','content':[{'type':'text','text':'Visible request'}]}}
            ]}
            return {}
    monkeypatch.setattr('oneai.sessions.observe.JsonLines',RPC)
    observer=Observer('codex',tmp_path,{'session_id':'bound','thread_id':'existing'},tmp_path,lambda k,p:emitted.append((k,p)))
    observer.step();before=len(emitted);observer.step();observer.close()
    assert len(emitted)==before
    assert [p['text'] for k,p in emitted if k=='message']==['Visible request','Visible reply']
    assert set(methods)<={'initialize','account/read','thread/read','thread/items/list'}
    assert 'private internal reasoning' not in json.dumps(emitted)

def test_stop_waits_until_prompt_is_written(tmp_path,monkeypatch):
    from oneai.sessions.host import Host
    writing=threading.Event();release=threading.Event();order=[]
    class Adapter:
        def __init__(self,*args):self.remote_id='r'
        def prompt(self,text,on_sent):writing.set();release.wait(3);order.append('prompt');on_sent()
        def interrupt(self):order.append('interrupt')
        def close(self):pass
    monkeypatch.setitem(__import__('oneai.sessions.host',fromlist=['ADAPTERS']).ADAPTERS,'codex',Adapter)
    host=Host({'providers':['codex'],'workspaces':{'p':str(tmp_path)},'binaries':{'codex':'unused'}},tmp_path/'state')
    prompt={'id':'p','session_id':'s','provider':'codex','workspace':'p','payload':{'action':'prompt','text':'hello'}}
    stop={**prompt,'id':'stop','payload':{'action':'interrupt'}}
    commands=[prompt,stop]
    host.request=lambda route,data: {'command':commands.pop(0)} if route=='poll' else {}
    try:
        host.step();assert writing.wait(3)
        host.step();assert not order
        release.set();host.pool.shutdown(wait=True)
        assert order==['prompt','interrupt']
    finally:release.set();host.close()
