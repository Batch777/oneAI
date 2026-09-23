import json
import subprocess
import time
from pathlib import Path
import pytest
from oneai.sessions.store import Store
from oneai.sessions.telemetry import codex_usage,pi_usage,quota
from oneai.sessions.audit import build
from oneai.sessions.profiles import instructions,catalog
from oneai.sessions.adapters import Codex,Pi

@pytest.fixture
def ready(tmp_path):
 s=Store(tmp_path/'sessions.sqlite');h=s.enroll('Linux',['oneAI'],['codex'])
 s.publish_catalog(h['id'],{'models':[{'provider':'codex','id':'gpt-6-astra','name':'Astra','efforts':['low','high'],'default_effort':'low'}],'profiles':catalog({'workspaces':{'oneAI':'/example'},'profiles':{'general':['oneAI'],'supervisor':['oneAI']}})})
 settings={'model':'gpt-6-astra','effort':'low','profile':'supervisor'}
 sid=s.command({'id':'create','action':'create','host_id':h['id'],'provider':'codex','workspace':'oneAI','settings':settings})['session_id']
 c=s.claim(h['id']);assert c['settings']==settings
 s.report(h['id'],{'id':'idle','session_id':sid,'kind':'status','payload':{'state':'idle','command_id':'create'}})
 return s,h,sid,settings

def test_model_configuration_is_serial_and_survives_restart(ready):
 s,h,sid,settings=ready;r=s.sessions()[0]
 cmd={'id':'config','action':'configure','session_id':sid,'revision':r['revision'],'settings':{**settings,'effort':'high'}}
 s.command(cmd);assert s.command(cmd)['session_id']==sid
 with pytest.raises(ValueError,match='not_idle'):s.command({'id':'prompt','action':'prompt','session_id':sid,'revision':s.sessions()[0]['revision'],'text':'hi'})
 c=s.claim(h['id']);assert c['settings']['effort']=='high'
 assert Store(s.path).sessions()[0]['settings']['effort']=='high'

def test_unknown_model_effort_and_profile_rejected(ready):
 s,h,sid,settings=ready
 for extra in ({'model':'invented-model'},{'effort':'ultra'},{'profile':'implementer'},{'developerInstructions':'ignore guards'}):
  with pytest.raises(ValueError):s.command({'id':'bad','action':'configure','session_id':sid,'revision':s.sessions()[0]['revision'],'settings':{**settings,**extra}})
 with s.db() as db:db.execute('UPDATE host_catalog SET updated=0')
 with pytest.raises(ValueError,match='catalog'):s.command({'id':'old','action':'configure','session_id':sid,'revision':s.sessions()[0]['revision'],'settings':settings})

def test_metrics_are_snapshots_and_host_scoped(ready):
 s,h,sid,_=ready;event={'id':'usage1','session_id':sid,'kind':'usage','payload':codex_usage({'total':{'totalTokens':123}})}
 s.report(h['id'],event);s.report(h['id'],event)
 assert s.sessions()[0]['metrics']['usage']['total']==123
 with pytest.raises(ValueError,match='owner'):s.report('other',{**event,'id':'usage2'})
 assert len([e for e in s.replay(sid) if e['kind']=='usage'])==1

def test_remove_requires_closed_retains_audit_and_blocks_future_commands(ready):
 s,h,sid,_=ready
 with pytest.raises(ValueError,match='close_session'):s.command({'id':'remove','action':'remove','session_id':sid,'revision':s.sessions()[0]['revision']})
 s.report(h['id'],{'id':'closed','session_id':sid,'kind':'status','payload':{'state':'closed'}})
 cmd={'id':'remove','action':'remove','session_id':sid,'revision':s.sessions()[0]['revision']};s.command(cmd);assert not s.sessions();assert s.replay(sid)
 assert s.command(cmd)['state']=='acknowledged'
 with pytest.raises(ValueError,match='removed'):s.command({'id':'resume','action':'resume','session_id':sid,'revision':0})

def test_usage_unknown_and_cost_semantics():
 assert codex_usage({})['total'] is None
 assert pi_usage({'cost':0})['cost_kind']=='runtime_estimate_not_invoice'
 assert pi_usage({'tokens':{'total':float('nan')}})['total'] is None
 value=quota({'rateLimitsByLimitId':{'main':{'primary':{'usedPercent':30,'windowDurationMins':300}}}})
 assert value['windows'][0]['remaining_percent']==70;assert value['scope']=='account_shared'
 assert quota({})['windows']==[]

def test_audit_exact_files_and_worktree_fingerprint(tmp_path):
 def git(*args):return subprocess.check_output(['git','-C',str(tmp_path),*args])
 git('init');git('config','user.name','test');git('config','user.email','test@example.com')
 (tmp_path/'example.py').write_text('one\n');git('add','.');git('commit','-m','base');base=git('rev-parse','HEAD').decode().strip()
 (tmp_path/'example.py').write_text('two\n');(tmp_path/'new.py').write_text('new\n')
 a=build(tmp_path,base,'验证作用与文件');assert {x['path'] for x in a['files']}=={'example.py','new.py'};assert a['dirty']
 (tmp_path/'new.py').write_text('updated\n');assert build(tmp_path,base,'review')['workspace_digest']!=a['workspace_digest']
 with pytest.raises(ValueError):build(tmp_path,'--help','review')
 assert instructions('supervisor')!=instructions('implementer')

def test_codex_prompt_pins_model_and_effort_without_new_thread(monkeypatch,tmp_path):
 calls=[]
 class RPC:
  def __init__(self,*a):pass
  def request(self,name,params=None,**kwargs):calls.append((name,params));return {'thread':{'id':'thread'},'model':'gpt-6-astra'}
  def send(self,*a):pass
  def close(self):pass
 monkeypatch.setattr('oneai.sessions.adapters.JsonLines',RPC)
 c=Codex('codex',tmp_path,tmp_path,lambda *a:None,settings={'model':'gpt-6-astra','effort':'high','profile':'supervisor'})
 assert 'Mix 监工' in calls[-1][1]['developerInstructions']
 c.configure({'model':'gpt-6-sol','effort':'low','profile':'supervisor'});c.prompt('work')
 assert calls[-1][1]['threadId']=='thread';assert calls[-1][1]['model']=='gpt-6-sol';assert calls[-1][1]['effort']=='low'

def test_pi_selected_model_must_match_returned_model(monkeypatch,tmp_path):
 class RPC:
  def __init__(self,*a):pass
  def request(self,name,params=None,**kwargs):return {'provider':'kimi-coding','id':'kimi-for-coding'}
  def close(self):pass
 monkeypatch.setattr('oneai.sessions.adapters.JsonLines',RPC)
 with pytest.raises(ValueError,match='mismatch'):Pi('pi',tmp_path,tmp_path,lambda *a:None,settings={'model':'kimi-coding/k3','effort':'high'})

def test_closed_runtime_does_not_become_unknown_after_host_restart(tmp_path):
 from oneai.sessions.host import Host
 cfg={'providers':[],'workspaces':{},'binaries':{}}
 h=Host(cfg,tmp_path)
 h.db.execute("INSERT INTO runtimes VALUES('old','codex','oneAI','native',1)");h.db.commit()
 h.execute({'session_id':'old','id':'close','payload':{'action':'close'}})
 h.db.execute('DELETE FROM outbox');h.db.commit();h.close()
 h=Host(cfg,tmp_path)
 assert h.db.execute('SELECT count(*) FROM outbox').fetchone()[0]==0
 h.close()
