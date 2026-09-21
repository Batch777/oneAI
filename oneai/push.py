"""Private Web Push subscriptions and a durable, bounded notification outbox."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit
from .web.auth import database


def encode(raw):return base64.urlsafe_b64encode(raw).decode().rstrip('=')
def decode(text):
    if not isinstance(text,str) or not re.fullmatch(r'[A-Za-z0-9_-]{10,100}',text):raise ValueError('Invalid subscription key')
    return base64.urlsafe_b64decode(text+'='*(-len(text)%4))


def initialize(cfg):
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives import serialization
    cfg.ensure_dirs();path=cfg.state_path/'webpush-private.pem'
    if not path.exists():
        key=ec.generate_private_key(ec.SECP256R1())
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(pem)
        except FileExistsError:pass
    return public_key(cfg)


def public_key(cfg):
    path=cfg.state_path/'webpush-private.pem'
    if not path.exists():return None
    from cryptography.hazmat.primitives import serialization
    key=serialization.load_pem_private_key(path.read_bytes(),password=None)
    return encode(key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint))


def validate(subscription):
    from cryptography.hazmat.primitives.asymmetric import ec
    if not isinstance(subscription,dict):raise ValueError('Invalid subscription')
    endpoint=subscription.get('endpoint')
    if not isinstance(endpoint,str) or len(endpoint)>2048:raise ValueError('Invalid endpoint')
    try:u=urlsplit(endpoint);host=u.hostname or '';port=u.port
    except ValueError:raise ValueError('Invalid endpoint')
    allowed=(host=='fcm.googleapis.com' or host.endswith('.push.apple.com') or host=='push.apple.com' or host.endswith('.push.services.mozilla.com'))
    if not allowed or u.scheme!='https' or u.username or u.password or port not in (None,443) or u.fragment:raise ValueError('Untrusted push service')
    keys=subscription.get('keys') or {}
    try:
        point=decode(keys['p256dh']);auth=decode(keys['auth'])
        if len(point)!=65 or len(auth)!=16:raise ValueError()
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),point)
    except (KeyError,TypeError,ValueError):raise ValueError('Invalid subscription keys')
    return {'endpoint':endpoint,'keys':{'p256dh':keys['p256dh'],'auth':keys['auth']}}


@contextmanager
def store(cfg):
    with database(cfg) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS push_subscriptions(device_id TEXT PRIMARY KEY,endpoint TEXT UNIQUE NOT NULL,subscription TEXT NOT NULL,created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS push_outbox(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,event_id TEXT NOT NULL,payload TEXT NOT NULL,expires REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,claimed REAL NOT NULL DEFAULT 0,last_error TEXT);
        ''')
        yield db


def subscribe(cfg,device_id,subscription):
    data=validate(subscription)
    with store(cfg) as db,db:
        owner=db.execute('SELECT device_id FROM push_subscriptions WHERE endpoint=?',(data['endpoint'],)).fetchone()
        if owner and owner[0]!=device_id:raise ValueError('Subscription belongs to another device; disable it there first')
        db.execute('INSERT INTO push_subscriptions VALUES(?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET endpoint=excluded.endpoint,subscription=excluded.subscription,created=excluded.created',(device_id,data['endpoint'],json.dumps(data),time.time()))


def unsubscribe(cfg,device_id):
    with store(cfg) as db,db:
        db.execute('DELETE FROM push_subscriptions WHERE device_id=?',(device_id,))
        db.execute("UPDATE push_outbox SET status='cancelled' WHERE device_id=? AND status IN ('pending','sending')",(device_id,))


def enqueue(cfg,device_id,event_id,expires,test=False):
    now=time.time();key=hashlib.sha256((device_id+':'+event_id).encode()).hexdigest()
    # No mailbox body, subject, sender, code, or tokenized URL leaves the server.
    payload={'title':'oneAI 测试通知' if test else 'oneAI · 收到验证邮件','body':'锁屏推送已连接。' if test else '解锁后查看验证码或验证链接。','tag':'oneai-'+event_id,'url':'/#mail-alerts'}
    with store(cfg) as db,db:
        if test:
            last=db.execute("SELECT max(expires) FROM push_outbox WHERE device_id=? AND event_id LIKE 'test:%'",(device_id,)).fetchone()[0]
            if last and last-900>now-60:raise ValueError('Wait one minute before another test')
        db.execute('INSERT OR IGNORE INTO push_outbox(id,device_id,event_id,payload,expires) VALUES(?,?,?,?,?)',(key,device_id,event_id,json.dumps(payload,ensure_ascii=False),expires))
    return key


def queue_recent(cfg):
    from .mailalerts import recent_alerts
    import datetime as dt
    now=time.time()
    with store(cfg) as db,db:
        db.execute('DELETE FROM push_subscriptions WHERE device_id NOT IN (SELECT id FROM sessions WHERE expires>?)',(now,))
        db.execute('DELETE FROM push_outbox WHERE expires<?',(now-86400,))
        subscribers=[dict(r) for r in db.execute('SELECT device_id,created FROM push_subscriptions')]
    count=0
    for alert in recent_alerts(cfg):
        received=dt.datetime.fromisoformat(alert['received'].replace('Z','+00:00')).timestamp()
        for sub in subscribers:
            if received<sub['created']:continue
            enqueue(cfg,sub['device_id'],alert['id'],received+900);count+=1
    return count


def deliver(cfg,subscription,payload,ttl):
    import requests
    from pywebpush import webpush
    class NoRedirect(requests.Session):
        def request(self,*args,**kwargs):
            kwargs['allow_redirects']=False
            return super().request(*args,**kwargs)
    with NoRedirect() as session:
        webpush(validate(subscription),data=payload,vapid_private_key=str(cfg.state_path/'webpush-private.pem'),
                vapid_claims={'sub':os.environ.get('ONEAI_PUSH_SUBJECT','https://47.82.117.21')},
                timeout=8,ttl=ttl,requests_session=session)


def dispatch(cfg,sender=deliver,limit=10):
    if not (cfg.state_path/'webpush-private.pem').exists():return {'configured':False}
    result={'sent':0,'failed':0,'expired':0};now=time.time()
    with store(cfg) as db,db:
        db.execute("UPDATE push_outbox SET status='expired' WHERE expires<=? AND status IN ('pending','sending')",(now,))
        db.execute("UPDATE push_outbox SET status='pending' WHERE status='sending' AND claimed<?",(now-120,))
    for _ in range(limit):
        with store(cfg) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT o.*,s.subscription FROM push_outbox o JOIN push_subscriptions s ON s.device_id=o.device_id JOIN sessions a ON a.id=s.device_id WHERE o.status='pending' AND o.next_attempt<=? AND o.expires>? AND a.expires>? ORDER BY o.expires LIMIT 1",(time.time(),time.time(),time.time())).fetchone()
            if not row:db.commit();break
            row=dict(row)
            db.execute("UPDATE push_outbox SET status='sending',claimed=?,attempts=attempts+1 WHERE id=?",(time.time(),row['id']));db.commit()
        try:
            sender(cfg,json.loads(row['subscription']),row['payload'],max(1,int(row['expires']-time.time())))
        except Exception as error:
            response=getattr(error,'response',None);code=getattr(response,'status_code',None)
            with store(cfg) as db,db:
                if code in (404,410):
                    db.execute('DELETE FROM push_subscriptions WHERE device_id=?',(row['device_id'],))
                    db.execute("UPDATE push_outbox SET status='expired',last_error='subscription_expired' WHERE id=?",(row['id'],))
                    result['expired']+=1
                else:
                    status='failed' if row['attempts']>=4 else 'pending'
                    db.execute('UPDATE push_outbox SET status=?,next_attempt=?,last_error=? WHERE id=?',(status,time.time()+min(300,30*2**row['attempts']),'delivery_failed',row['id']))
                    result['failed']+=1
        else:
            with store(cfg) as db,db:db.execute("UPDATE push_outbox SET status='sent',last_error=NULL WHERE id=?",(row['id'],))
            result['sent']+=1
    return result


def main():
    import argparse
    from .config import Config
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['init','run']);a=p.parse_args();cfg=Config.load()
    if a.action=='init':print('Web Push public key:',initialize(cfg))
    else:queue_recent(cfg);print(json.dumps(dispatch(cfg)))

if __name__=='__main__':main()
