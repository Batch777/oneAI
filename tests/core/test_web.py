import pytest
from fastapi.testclient import TestClient
from oneai.config import Config
from oneai.web.app import create_app
from oneai.web import auth
from oneai.worker import tick

@pytest.fixture
def client(tmp_path):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None)
    app=create_app(cfg,origin='https://testserver')
    c=TestClient(app,base_url='https://testserver')
    c.headers['origin']='https://testserver'
    return cfg,c

def login(cfg,c):
    response=c.post('/api/login',json={'code':auth.pair(cfg),'name':'test'})
    assert response.status_code==200
    c.headers['x-oneai-csrf']=response.json()['csrf']
    return response.json()

def test_auth_boundaries(client):
    cfg,c=client
    assert c.get('/api/tasks').status_code==401
    code=auth.pair(cfg)
    assert c.post('/api/login',json={'code':code},headers={'origin':'https://evil.example'}).status_code==403
    response=c.post('/api/login',json={'code':code})
    assert response.status_code==200
    cookie=response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    assert c.post('/api/login',json={'code':code}).status_code==401
    assert c.post('/api/pair',json={}).status_code==403
    c.headers['x-oneai-csrf']=response.json()['csrf']
    assert c.post('/api/pair',json={}).status_code==200
    assert c.get('/api/tasks').headers['cache-control']=='no-store'
    assert c.get('/tasks.sqlite').status_code==404
    assert c.get('/',headers={'host':'evil.example'}).status_code==400
    assert c.post('/api/logout',json={}).status_code==200
    assert c.get('/api/tasks').status_code==401

def test_task_revision_and_retry(client):
    cfg,c=client; login(cfg,c)
    command={'id':'create-1','action':'create','title':'Simulator validation','body':'Prepare draft'}
    r=c.post('/api/commands',json=command); assert r.status_code==200
    tid=r.json()['task_id']
    assert c.post('/api/commands',json=command).status_code==200
    assert c.get('/api/tasks').json()['total']==1
    assert c.post('/api/commands',json={**command,'body':'changed'}).status_code==409
    tick(cfg)
    row=c.get('/api/tasks/'+tid).json(); assert row['status']=='needs_review'
    assert c.post('/api/commands',json={'id':'rev','action':'revise','task_id':tid,'revision':1,'body':'Edited'}).status_code==200
    assert c.post('/api/commands',json={'id':'stale','action':'approve','task_id':tid,'revision':1}).status_code==409
    assert c.post('/api/commands',json={'id':'ok','action':'approve','task_id':tid,'revision':2}).status_code==200
    assert c.get('/api/tasks/'+tid).json()['status']=='reviewed'
    assert c.post('/api/commands',json={'id':'send','action':'send'}).status_code==400

def test_pair_rate_limit_and_revoke(client):
    cfg,c=client; user=login(cfg,c)
    for _ in range(10): c.post('/api/login',json={'code':'wrong'})
    assert c.post('/api/login',json={'code':'wrong'}).status_code==429
    assert c.post('/api/devices/revoke',json={'id':user['device_id']}).status_code==200
    assert c.get('/api/session').status_code==401

def test_historical_source_and_bounded_requests(client):
    cfg,c=client; login(cfg,c)
    path=cfg.vault_path/'research.md'
    path.write_text('# Gaussian research\n\nGaussian evidence from a local research note, preserved as a versioned source.\n')
    tick(cfg)
    results=c.get('/api/search',params={'q':'Gaussian'}).json()['items']
    assert results
    citation=results[0]['citation']
    path.write_text('Changed after citation')
    source=c.get('/api/source',params={'citation':citation})
    assert source.status_code==200 and 'Gaussian evidence' in source.json()['text']
    assert c.get('/api/source',params={'citation':'../../outlook.env'}).status_code==400
    assert c.post('/api/commands',content=b'x'*200001,headers={'content-type':'application/json'}).status_code==413

def test_four_digit_pair_replaces_old_code_and_expires(client,monkeypatch):
    cfg,c=client
    monkeypatch.setattr(auth.time,'time',lambda:1000)
    old=auth.pair(cfg);new=auth.pair(cfg)
    assert len(new)==4 and new.isascii() and new.isdigit() and old!=new
    assert c.post('/api/login',json={'code':old}).status_code==401
    assert c.post('/api/login',json={'code':new}).status_code==200
    assert c.post('/api/login',json={'code':new}).status_code==401
    expired=auth.pair(cfg)
    monkeypatch.setattr(auth.time,'time',lambda:1601)
    assert c.post('/api/login',json={'code':expired}).status_code==401

def test_short_code_failed_attempt_budget_survives_regeneration(client):
    cfg,c=client
    for _ in range(5):
        assert c.post('/api/login',json={'code':'invalid'}).status_code==401
    code=auth.pair(cfg)
    assert c.post('/api/login',json={'code':code}).status_code==429

def test_short_code_global_failed_attempt_budget(client):
    cfg,c=client
    for i in range(10):
        with pytest.raises(ValueError,match='invalid_code'):
            auth.login(cfg,'invalid','test',str(i))
    code=auth.pair(cfg)
    with pytest.raises(ValueError,match='too_many_attempts'):
        auth.login(cfg,code,'test','another-ip')

def test_filtered_mail_visible_restorable_and_counts_match(client):
    from oneai.tasks import Tasks
    from oneai.mailtriage import classify_pending
    cfg,c=client;login(cfg,c)
    store=Tasks(cfg.state_path/'tasks.sqlite')
    tid=store.create('mail:test-otp','验证码','验证码为 123456')
    classify_pending(store);store.db.close()
    assert c.get('/api/tasks').json()['total']==0
    assert c.get('/api/tasks',params={'mail_view':'filtered'}).json()['total']==1
    detail=c.get('/api/tasks/'+tid).json()
    assert detail['verification']['code']=='123456' and detail['mail_classification']['filtered']==1
    assert c.get('/api/status').json()['counts']=={}
    assert c.post('/api/commands',json={'id':'restore-1','action':'restore_mail','task_id':tid}).status_code==200
    assert c.get('/api/tasks').json()['total']==1
    assert c.get('/api/tasks',params={'mail_view':'filtered'}).json()['total']==0
    assert c.get('/api/mail/alerts').json()=={'items':[]}


def test_reply_generation_is_explicit_and_revision_safe(client):
    cfg,c=client;login(cfg,c)
    tid=c.post('/api/commands',json={'id':'reply-create','action':'create','title':'回复测试','body':'请确认研究安排'}).json()['task_id']
    tick(cfg)
    before=c.get('/api/tasks/'+tid).json()
    assert not before['reply_generated'] and '## 回复草稿模板' not in before['result']
    command={'id':'reply-generate','action':'generate_reply','task_id':tid,'revision':before['revision']}
    assert c.post('/api/commands',json=command).status_code==200
    assert c.post('/api/commands',json=command).status_code==200
    after=c.get('/api/tasks/'+tid).json()
    assert after['reply_generated'] and after['result'].count('## 回复草稿')==1
    assert after['revision']==before['revision']+1
    assert c.post('/api/commands',json={**command,'id':'stale-device'}).status_code==409
    assert c.post('/api/commands',json={**command,'id':'duplicate','revision':after['revision']}).status_code==409
    assert 'identity' in after['context_view']


def test_push_api_boundaries_and_logout(client):
    from oneai import push
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    cfg,c=client
    assert c.get('/api/push/config').status_code==401
    login(cfg,c)
    assert c.post('/api/push/test',json={}).status_code==409
    push.initialize(cfg)
    point=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    sub={'endpoint':'https://fcm.googleapis.com/fcm/send/test','keys':{'p256dh':push.encode(point),'auth':push.encode(b'a'*16)}}
    assert c.post('/api/push/subscribe',json=sub,headers={'x-oneai-csrf':'wrong'}).status_code==403
    assert c.post('/api/push/subscribe',json=sub).status_code==200
    assert c.get('/api/push/config').json()['subscribed']
    assert c.post('/api/push/test',json={}).status_code==200
    assert c.post('/api/push/test',json={}).status_code==429
    assert c.post('/api/logout',json={}).status_code==200
    with push.store(cfg) as db:
        assert db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0]==0
        assert db.execute('SELECT status FROM push_outbox').fetchone()[0]=='cancelled'
