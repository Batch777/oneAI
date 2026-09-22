import datetime as dt
import json
import sqlite3
from oneai.config import Config
from oneai.tasks import Tasks
from oneai.mailtriage import classify,classify_pending,save
from oneai.mailalerts import verification_hints,recent_alerts


def test_filter_only_strong_noise_and_keep_security_alerts():
    assert classify('验证码','验证码为 345678').filtered
    assert classify('<广告> 限时优惠','点击此处退订').filtered
    assert not classify('项目截止通知','验证码为 345678').filtered
    assert not classify('Security alert: unusual sign-in','verification code: 123456').filtered
    assert not classify('会议确认','请确认明天的会议，unsubscribe').filtered
    assert not classify('限时优惠','请检查报价单').filtered
    assert not classify('Verification code delivery failed','Service report for 2026').filtered


def test_ai_contract_failure_and_protected_messages():
    model=lambda _:json.dumps({'category':'promotion','confidence':.99,'reason':'推广消息'})
    assert classify('newsletter','some text',model).filtered
    assert not classify('invoice','some text',model).filtered
    assert not classify('newsletter','text',lambda _:'ignore all rules').filtered
    assert not classify('newsletter','text',lambda _:json.dumps({'category':'promotion','confidence':float('nan'),'reason':'x'})).filtered
    assert not classify('newsletter','text',lambda _:json.dumps({'category':'promotion','confidence':.97,'reason':'x'})).filtered


def test_classification_is_idempotent_and_user_restore_wins(tmp_path):
    store=Tasks(tmp_path/'tasks.sqlite');tid=store.create('mail:1','验证码','验证码：345678')
    assert classify_pending(store)==1
    assert classify_pending(store)==0
    store.command({'id':'restore','action':'restore_mail','task_id':tid})
    save(store,tid,classify('验证码','验证码：345678'),replace=True)
    assert store.db.execute('SELECT filtered,source FROM mail_triage').fetchone()[:]==(0,'user')
    store.db.close()


def test_hints_ignore_incidental_numbers_and_unsafe_links():
    assert verification_hints('会议室','房间 123456')['code'] is None
    hints=verification_hints('Verify your email','Your verification code is 001234. https://accounts.example.com/verify?t=abc https://localhost/verify https://127.0.0.1/verify https://u:p@example.com/verify https://example.com:bad/verify javascript:alert(1)')
    assert hints['code']=='001234'
    assert hints['links']==[{'url':'https://accounts.example.com/verify?t=abc','domain':'accounts.example.com'}]


def test_only_recent_undeleted_mail_alerts(tmp_path):
    cfg=Config(tmp_path/'vault',tmp_path/'state',None);cfg.ensure_dirs()
    now=dt.datetime(2026,9,21,12,tzinfo=dt.timezone.utc)
    with sqlite3.connect(cfg.state_path/'outlook.sqlite') as db:
        db.execute('CREATE TABLE messages(id TEXT PRIMARY KEY,payload TEXT,deleted INTEGER)')
        for key,minutes,deleted in [('new',1,0),('old',60,0),('removed',1,1),('future',-1,0)]:
            m={'id':key,'subject':'验证码','bodyPreview':'验证码为 123456','receivedDateTime':(now-dt.timedelta(minutes=minutes)).isoformat()}
            db.execute('INSERT INTO messages VALUES(?,?,?)',(key,json.dumps(m),deleted))
    items=recent_alerts(cfg,now)
    assert len(items)==1 and items[0]['code']=='123456'


def test_standalone_code_after_verification_instructions():
    body='Welcome\nUse the code below to verify your email and create your account. It expires in 10 minutes.\n001234\nEnter the code on the requested page.'
    assert verification_hints('Your service verification code',body)['code']=='001234'
    assert classify('Your service verification code',body).filtered
    assert verification_hints('Your service verification code',body+'\n654321')['code'] is None
    assert verification_hints('Annual report','Report for the following year\n2026')['code'] is None
