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
