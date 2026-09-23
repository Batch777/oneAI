import json
import shutil
import subprocess
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from oneai.plugins.package import inspect,install,validate_input
from oneai.plugins.store import Store
from oneai.plugins.runner import execute,Runner
from oneai.config import Config
from oneai.sessions.store import Store as Hosts
from oneai.web.app import create_app
from oneai.web import auth

SOURCE=Path(__file__).parents[2]/'plugins/workspace-review'

@pytest.fixture
def package(tmp_path):
    p=tmp_path/'source';shutil.copytree(SOURCE,p);return p

@pytest.fixture
def registry(tmp_path,package):
    m,d=inspect(package);s=Store(tmp_path/'index.sqlite');s.publish('linux',[{'manifest':m,'digest':d}]);p=s.list()[0]
    s.toggle(p['key'],p['revision'],True);p=s.list()[0]
    call={'id':'run-1','plugin_key':p['key'],'revision':p['revision'],'workspace':'project','input':{'commits':1}}
    return s,call,m,d

def test_packages_are_content_addressed_and_reject_links(package,tmp_path):
    m,d=install(package,tmp_path/'installed');assert inspect(tmp_path/'installed/packages'/d)==(m,d)
    assert install(package,tmp_path/'installed')==(m,d)
    (package/'main.py').write_text('changed');assert inspect(package)[1]!=d
    (package/'leak').symlink_to('/etc/passwd')
    with pytest.raises(ValueError,match='links'):inspect(package)

@pytest.mark.parametrize('value',[{}, {'commits':True},{'commits':11},{'commits':1,'path':'/etc/passwd'}])
def test_input_schema_rejects_invalid_values(package,value):
    m,_=inspect(package)
    with pytest.raises(ValueError):validate_input(m['input_schema'],value)

def test_dispatch_idempotency_owner_and_result(registry):
    s,c,m,d=registry
    assert s.invoke(c,{'linux':['project']})['status']=='queued'
    assert s.invoke(c,{'linux':['project']})['id']==c['id']
    with pytest.raises(ValueError,match='conflict'):s.invoke({**c,'input':{'commits':2}},{'linux':['project']})
    assert s.claim('other') is None
    claimed=s.claim('linux');assert claimed['digest']==d
    assert s.claim('linux') is None
    result={'id':c['id'],'status':'completed','result':{'ok':True}}
    with pytest.raises(ValueError,match='owner'):s.result('other',result)
    s.result('linux',result);s.result('linux',result)
    assert s.runs()[0]['status']=='completed'

def test_upgrade_disables_and_cancels_queued(registry):
    s,c,m,d=registry;s.invoke(c,{'linux':['project']})
    s.publish('linux',[{'manifest':m,'digest':'f'*64}]);p=s.list()[0]
    assert not p['enabled'];assert s.runs()[0]['status']=='cancelled';assert s.claim('linux') is None
    with pytest.raises(ValueError,match='stale'):s.toggle(p['key'],c['revision'],True)

def test_disable_drains_inflight_and_uncertain_work_not_replayed(registry):
    s,c,m,d=registry;s.invoke(c,{'linux':['project']});s.claim('linux')
    s.toggle(c['plugin_key'],c['revision'],False)
    with s.db() as db:db.execute('UPDATE plugin_runs SET deadline=0')
    assert s.runs()[0]['status']=='unknown';assert s.claim('linux') is None
    s.result('linux',{'id':c['id'],'status':'completed','result':{'done':True}})
    assert s.runs()[0]['status']=='completed'

def test_workspace_and_revision_enforced(registry):
    s,c,_,_=registry
    with pytest.raises(ValueError,match='workspace'):s.invoke(c,{'linux':['other']})
    with pytest.raises(ValueError,match='stale'):s.invoke({**c,'revision':0},{'linux':['project']})

def test_real_git_plugin_and_digest_pin(package,tmp_path):
    repo=tmp_path/'repo';repo.mkdir();subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=test','-c','user.email=test@example.com','commit','--allow-empty','-m','plugin smoke'],check=True,capture_output=True)
    m,d=inspect(package);c={'id':'smoke','manifest':m,'digest':d,'input':{'commits':1}}
    result=execute(package,c,repo);assert result['changed_files']==0;assert 'plugin smoke' in result['recent_commits'][0]
    (package/'main.py').write_text('raise Exception()')
    with pytest.raises(ValueError,match='digest'):execute(package,c,repo)

@pytest.mark.parametrize('script,error',[("import time; time.sleep(5)",TimeoutError),("print('x'*65000)",ValueError),("print('{}')",ValueError)])
def test_bounded_process_and_protocol(package,tmp_path,script,error):
    (package/'main.py').write_text(script);m=json.loads((package/'plugin.json').read_text());m['timeout_seconds']=1;(package/'plugin.json').write_text(json.dumps(m));m,d=inspect(package)
    with pytest.raises(error):execute(package,{'id':'bad','manifest':m,'digest':d,'input':{'commits':1}},tmp_path)

def test_runner_crash_receipt_never_reexecutes(tmp_path):
    r=Runner({},tmp_path);r.db.execute("INSERT INTO runs VALUES('uncertain',NULL)");r.db.commit();r.close()
    r=Runner({},tmp_path);payload=json.loads(r.db.execute('SELECT payload FROM runs').fetchone()[0]);assert payload['status']=='unknown';r.close()

def test_plugin_routes_separate_host_and_browser_credentials(tmp_path):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None);app=create_app(cfg,origin='https://testserver')
    c=TestClient(app,base_url='https://testserver');c.headers['origin']='https://testserver'
    assert c.get('/api/plugins').status_code==401
    response=c.post('/api/login',json={'code':auth.pair(cfg)});c.headers['x-oneai-csrf']=response.json()['csrf']
    hstore=Hosts(cfg.state_path/'sessions.sqlite');h=hstore.enroll('Linux',['project'],['codex'])
    remote=TestClient(app,base_url='https://testserver');remote.headers['authorization']='Bearer '+h['token']
    assert remote.post('/api/agent-host/plugins/poll',json={}).status_code==403
    with hstore.db() as db:
        caps={**hstore.hosts()[0]['capabilities'],'plugins':True};db.execute('UPDATE session_hosts SET capabilities=?',(json.dumps(caps),))
    m,d=inspect(SOURCE)
    assert remote.post('/api/agent-host/plugins/inventory',json={'items':[{'manifest':m,'digest':d}]}).status_code==200
    assert remote.get('/api/plugins').status_code==401
    assert c.post('/api/agent-host/plugins/poll',json={}).status_code==403
    p=c.get('/api/plugins').json()['items'][0]
    assert not p['enabled']
    assert c.post('/api/plugins/'+p['key']+'/state',json={'revision':0,'enabled':True},headers={'origin':'https://evil.example'}).status_code==403
    assert c.post('/api/plugins/'+p['key']+'/state',json={'revision':0,'enabled':True}).status_code==200

def test_explicit_runtime_policy(monkeypatch,tmp_path):
    from oneai.sessions import adapters
    calls=[]
    class RPC:
        def __init__(self,argv,*a):calls.append(argv)
        def request(self,name,params=None,**kw):calls.append((name,params));return {'thread':{'id':'existing'}}
        def send(self,*a):pass
        def close(self):pass
    monkeypatch.setattr(adapters,'JsonLines',RPC)
    adapters.Codex('codex',tmp_path,tmp_path,lambda *a:None,policy='full')
    assert calls[-1][1]['sandbox']=='danger-full-access';assert calls[-1][1]['approvalPolicy']=='never'
    adapters.Pi('pi',tmp_path,tmp_path,lambda *a:None,policy='full')
    assert 'read,bash,edit,write,grep,find,ls' in calls[-2]
    with pytest.raises(ValueError):adapters.Pi('pi',tmp_path,tmp_path,lambda *a:None,policy='invalid')
