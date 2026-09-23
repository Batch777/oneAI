import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('manager', Path(__file__).parents[2]/'deploy/releases/manager.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


def candidate(root, sha):
    target = root/'releases'/sha; target.mkdir(parents=True)
    (target/'content').write_text(sha)
    m.write_json(target/'.release.json', {'commit':sha, 'files':m.files_digest(target)})
    return target


def test_switch_and_failed_candidate_restore_previous(tmp_path):
    old, new = 'a'*40, 'b'*40
    candidate(tmp_path, old); candidate(tmp_path, new)
    m.activate(tmp_path, old, lambda:None, lambda sha:True)
    calls=[]
    with pytest.raises(RuntimeError, match='candidate_health_failed'):
        m.activate(tmp_path, new, lambda:calls.append('restart'), lambda sha:sha==old)
    assert (tmp_path/'current').resolve().name==old
    assert len(calls)==2
    assert json.loads((tmp_path/'deployment.json').read_text())['phase']=='rolled_back'


def test_tampered_candidate_does_not_switch(tmp_path):
    target=candidate(tmp_path, 'a'*40); (target/'content').write_text('changed')
    with pytest.raises(ValueError, match='integrity'):
        m.activate(tmp_path, target.name, lambda:pytest.fail(), lambda sha:True)
    assert not (tmp_path/'current').exists()


def test_crash_requires_explicit_recovery(tmp_path):
    old,new='a'*40,'b'*40
    candidate(tmp_path,old); candidate(tmp_path,new)
    m.switch(tmp_path,new)
    m.write_json(tmp_path/'deployment.json', {'previous':old,'candidate':new,'phase':'switching'})
    with pytest.raises(RuntimeError, match='requires_recover'):
        m.activate(tmp_path,new,lambda:None,lambda sha:True)
    m.recover(tmp_path,lambda:None,lambda sha:sha==old)
    assert (tmp_path/'current').resolve().name==old


def test_rollback_failure_is_visible(tmp_path):
    old,new='a'*40,'b'*40
    candidate(tmp_path,old); candidate(tmp_path,new);m.switch(tmp_path,old)
    with pytest.raises(RuntimeError,match='rollback_health_failed'):
        m.activate(tmp_path,new,lambda:None,lambda sha:False)
    assert json.loads((tmp_path/'deployment.json').read_text())['phase']=='rollback_failed'


def test_activate_rejects_non_commit_selector(tmp_path):
    with pytest.raises(ValueError, match='exact_commit_required'):
        m.activate(tmp_path, '../candidate', lambda:pytest.fail(), lambda sha:True)
    assert not (tmp_path/'deployment.json').exists()
