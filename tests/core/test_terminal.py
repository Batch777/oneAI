import json
import pytest
from oneai.terminal import Client


def test_terminal_requires_https_and_retains_ambiguous_write(tmp_path):
    with pytest.raises(ValueError): Client('http://example.com',tmp_path/'bad')
    client=Client('https://example.com',tmp_path/'client')
    def offline(*args): raise ConnectionError('offline')
    client.request=offline
    command={'id':'same-id','action':'create','title':'Test','body':'Body'}
    with pytest.raises(ConnectionError): client.command(command)
    assert json.loads(client.pending.read_text())==command
    with pytest.raises(ValueError,match='retry first'): client.command(command)
    seen=[]
    def online(path,body): seen.append(body); return {'accepted':True}
    client.request=online
    client.command()
    assert seen==[command] and not client.pending.exists()


def test_terminal_definitive_conflict_clears_pending(tmp_path):
    client=Client('https://example.com',tmp_path)
    def reject(*args): raise ValueError('409: Stale revision')
    client.request=reject
    with pytest.raises(ValueError): client.command({'id':'reject'})
    assert not client.pending.exists()

def test_default_cli_uses_cloud_terminal_without_pi(monkeypatch):
    import sys
    from oneai.cli import main
    calls=[]
    monkeypatch.setattr(sys,'argv',['oneai'])
    monkeypatch.setattr('oneai.terminal.main',lambda args:calls.append(args))
    main()
    assert calls==[[]]
