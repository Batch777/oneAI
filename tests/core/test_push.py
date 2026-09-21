import json
import time
from types import SimpleNamespace
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from oneai import push
from oneai.config import Config
from oneai.web import auth

@pytest.fixture
def setup(tmp_path):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None)
    public=push.initialize(cfg)
    assert push.initialize(cfg)==public
    assert (cfg.state_path/'webpush-private.pem').stat().st_mode & 0o777 == 0o600
    _,_,sid=auth.login(cfg,auth.pair(cfg),'test','local')
    point=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    sub={'endpoint':'https://fcm.googleapis.com/fcm/send/test','keys':{'p256dh':push.encode(point),'auth':push.encode(b'a'*16)}}
    push.subscribe(cfg,sid,sub)
    return cfg,sid,sub

@pytest.mark.parametrize('url',['http://fcm.googleapis.com/x','https://127.0.0.1/x','https://fcm.googleapis.com.evil.test/x','https://evil.push.apple.com.evil.test/x','https://u:p@push.apple.com/x','https://push.apple.com:444/x'])
def test_endpoint_validation(setup,url):
    _,_,sub=setup
    with pytest.raises(ValueError):push.validate({**sub,'endpoint':url})

def test_dedup_and_private_payload(setup):
    cfg,sid,_=setup
    key=push.enqueue(cfg,sid,'mail-1',time.time()+900)
    assert push.enqueue(cfg,sid,'mail-1',time.time()+900)==key
    sent=[]
    assert push.dispatch(cfg,lambda *args:sent.append(args))['sent']==1
    assert push.dispatch(cfg,lambda *args:sent.append(args))['sent']==0
    payload=json.loads(sent[0][2])
    assert payload['url']=='/#mail-alerts'
    assert set(payload)=={'title','body','tag','url'}
    assert len(sent)==1

def test_revocation_and_expiry(setup):
    cfg,sid,_=setup
    push.enqueue(cfg,sid,'expired',time.time()-1)
    push.enqueue(cfg,sid,'cancel',time.time()+900)
    push.unsubscribe(cfg,sid)
    assert push.dispatch(cfg,lambda *_:pytest.fail('revoked delivery'))['sent']==0

def test_session_expiry_blocks_delivery(setup):
    cfg,sid,_=setup
    push.enqueue(cfg,sid,'mail',time.time()+900)
    with auth.database(cfg) as db,db:db.execute('UPDATE sessions SET expires=0 WHERE id=?',(sid,))
    assert push.dispatch(cfg,lambda *_:pytest.fail('expired device'))['sent']==0

def test_retry_is_bounded_and_error_is_redacted(setup):
    cfg,sid,_=setup
    push.enqueue(cfg,sid,'mail',time.time()+900)
    def fail(*_):raise RuntimeError('secret code 123456')
    for i in range(5):
        assert push.dispatch(cfg,fail)['failed']==1
        with push.store(cfg) as db,db:
            row=db.execute('SELECT * FROM push_outbox').fetchone()
            assert row['attempts']==i+1 and row['last_error']=='delivery_failed'
            db.execute('UPDATE push_outbox SET next_attempt=0')
    assert row['status']=='failed'
    assert push.dispatch(cfg,fail)['failed']==0

def test_gone_endpoint_and_test_cooldown(setup):
    cfg,sid,_=setup
    push.enqueue(cfg,sid,'test:1',time.time()+900,test=True)
    with pytest.raises(ValueError):push.enqueue(cfg,sid,'test:2',time.time()+900,test=True)
    def gone(*_):
        error=RuntimeError('gone');error.response=SimpleNamespace(status_code=410);raise error
    assert push.dispatch(cfg,gone)['expired']==1
    with push.store(cfg) as db:assert db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0]==0

def test_queue_only_new_mail_and_no_historical_replay(setup,monkeypatch):
    import datetime as dt
    import oneai.mailalerts
    cfg,sid,_=setup
    now=time.time()
    def alert(key,t):return {'id':key,'received':dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat(),'code':'123456'}
    monkeypatch.setattr(oneai.mailalerts,'recent_alerts',lambda _: [alert('old',now-60),alert('new',now+1)])
    push.queue_recent(cfg);push.queue_recent(cfg)
    with push.store(cfg) as db:
        rows=db.execute('SELECT event_id,payload FROM push_outbox').fetchall()
        assert len(rows)==1 and rows[0][0]=='new' and '123456' not in rows[0][1]


def test_real_webpush_encrypts_and_disables_redirects(setup,monkeypatch):
    import requests
    cfg,_,sub=setup
    captured=[]
    def send(session,request,**kwargs):
        captured.append((request,kwargs))
        response=requests.Response();response.status_code=201;response._content=b'';response.request=request
        return response
    monkeypatch.setattr(requests.Session,'send',send)
    payload='{"title":"private synthetic notification"}'
    push.deliver(cfg,sub,payload,100)
    request,options=captured[0]
    assert request.method=='POST' and request.url==sub['endpoint']
    assert b'private synthetic notification' not in request.body
    assert request.headers['Content-Encoding']=='aes128gcm'
    assert request.headers['Authorization'].startswith('vapid ')
    assert options['allow_redirects'] is False and options['timeout']==8
