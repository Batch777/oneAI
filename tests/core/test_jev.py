import json
import httpx
import pytest
from oneai.jev import JevClassifier,CRITERIA
from oneai.mailtriage import classify

def transport(attention=0,invalid=False):
    def send(request):
        data=json.loads(request.content)
        assert request.url=='https://api.typesafe.ai/v1/systemone'
        assert data['model']=='jev-1.13.0' and len(data['questions'])==2
        assert '123456' not in data['state']['email_excerpt']
        assert 'secret-token' not in data['state']['email_excerpt']
        probs={key:(.999 if key=='promotion' else .00025) for key in CRITERIA}
        return httpx.Response(200,json={'answers':{'category':{'choice':'promotion','confidence':float('nan') if invalid else .999,'probabilities':probs},'attention':{'noul':attention}}})
    return httpx.MockTransport(send)

def test_jev_adapter_redaction_and_policy_threshold():
    result=classify('Newsletter','New products 123456 https://example.com/?secret-token=x',JevClassifier('test',transport()))
    assert result.source=='jev' and result.filtered
    result=classify('Newsletter','New products',JevClassifier('test',transport(.4)))
    assert result.confidence==.6 and not result.filtered

def test_jev_errors_retain_mail_and_protected_mail_skips_network():
    def fail(_):raise AssertionError('protected mail must not call Jev')
    assert not classify('invoice deadline','pay today',JevClassifier('test',httpx.MockTransport(fail))).filtered
    def malformed(_):return httpx.Response(200,json={'answers':{}})
    result=classify('hello','unknown',JevClassifier('test',httpx.MockTransport(malformed)))
    assert result.source=='fallback' and not result.filtered
    def limited(_):return httpx.Response(429)
    assert not classify('hello','unknown',JevClassifier('test',httpx.MockTransport(limited))).filtered

def test_jev_requires_key(monkeypatch):
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    with pytest.raises(ValueError):JevClassifier()

def test_jev_replaces_promotion_and_otp_rule_classification():
    calls=[]
    def uncertain(request):
        calls.append(request)
        return httpx.Response(200,json={'answers':{'category':{'choice':'uncertain','confidence':1,
            'probabilities':{k:int(k=='uncertain') for k in CRITERIA}},'attention':{'noul':0}}})
    model=JevClassifier('test',httpx.MockTransport(uncertain))
    for title,body in [('<广告> 限时优惠','unsubscribe'),('验证码','验证码为 001234')]:
        d=classify(title,body,model)
        assert d.source=='jev' and d.category=='uncertain' and not d.filtered
        assert d.evidence['model_confidence']==1
    assert len(calls)==2

def test_background_missing_key_leaves_mail_pending(tmp_path,monkeypatch):
    from oneai.config import Config
    from oneai.tasks import Tasks
    from oneai.triage_worker import tick
    cfg=Config(tmp_path/'vault',tmp_path/'state',None);cfg.ensure_dirs()
    store=Tasks(cfg.state_path/'tasks.sqlite');store.create('mail:a','hello','body')
    monkeypatch.delenv('TYPESAFE_API_KEY',raising=False)
    assert tick(cfg)['state']=='missing_key'
    assert store.db.execute('SELECT count(*) FROM mail_triage').fetchone()[0]==0
    store.db.close()

def test_background_batch_and_no_automatic_failure_retry(tmp_path):
    from oneai.config import Config
    from oneai.tasks import Tasks
    from oneai.triage_worker import tick
    cfg=Config(tmp_path/'vault',tmp_path/'state',None);cfg.ensure_dirs()
    store=Tasks(cfg.state_path/'tasks.sqlite')
    for n in range(3):store.create('mail:'+str(n),'unknown','body')
    factory=lambda:JevClassifier('test',httpx.MockTransport(lambda _:httpx.Response(429)))
    assert tick(cfg,factory)['classified']==2
    assert tick(cfg,factory)['classified']==1
    assert tick(cfg,factory)['classified']==0
    assert store.db.execute('SELECT sum(filtered) FROM mail_triage').fetchone()[0]==0
    store.db.close()

def test_concurrent_review_while_jev_runs_cannot_hide_mail(tmp_path):
    from oneai.tasks import Tasks
    from oneai.mailtriage import classify_pending
    store=Tasks(tmp_path/'tasks.sqlite');tid=store.create('mail:a','newsletter','body')
    store.finish(tid,'draft',[],[])
    def send(request):
        store.command({'id':'approve-during-network','action':'approve','task_id':tid,'revision':1})
        return transport().handle_request(request)
    classify_pending(store,model=JevClassifier('test',httpx.MockTransport(send)))
    assert store.db.execute('SELECT filtered FROM mail_triage').fetchone()[0]==0
    assert json.loads(store.db.execute('SELECT payload FROM mail_triage_evidence').fetchone()[0])['policy_score']==.999
    store.db.close()


def test_review_after_classification_also_clears_filter(tmp_path):
    from oneai.tasks import Tasks
    from oneai.mailtriage import classify_pending
    store=Tasks(tmp_path/'tasks.sqlite');tid=store.create('mail:a','newsletter','body')
    store.finish(tid,'draft',[],[])
    classify_pending(store,model=JevClassifier('test',transport()))
    assert store.db.execute('SELECT filtered FROM mail_triage').fetchone()[0]==1
    store.command({'id':'review-after-classification','action':'approve','task_id':tid,'revision':1})
    assert store.db.execute('SELECT filtered FROM mail_triage').fetchone()[0]==0
    store.db.close()
