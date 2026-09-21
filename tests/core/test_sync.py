import json
from pathlib import Path
import pytest
from oneai.config import Config
from oneai.sync import handle, sync_once, note_path, sha
from oneai.worker import tick


def config(root):
    c=Config(root/'vault',root/'state',None); c.ensure_dirs(); return c


def write(cfg,name,text):
    p=cfg.vault_path/name; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(text)


def test_bidirectional_offline_conflict_and_safe_deletion(tmp_path):
    mac=config(tmp_path/'mac'); cloud=config(tmp_path/'cloud')
    rpc=lambda req: handle(cloud,req)
    write(mac,'facts/a.md','v1')
    assert sync_once(mac,rpc)['uploaded']==1
    write(cloud,'facts/a.md','cloud v2')
    assert sync_once(mac,rpc)['downloaded']==1
    write(mac,'facts/a.md','mac offline'); write(cloud,'facts/a.md','cloud offline')
    assert sync_once(mac,rpc)['conflicts']==1
    assert (mac.vault_path/'facts/a.md').read_text()=='mac offline'
    assert (cloud.vault_path/'facts/a.md').read_text()=='cloud offline'
    assert list((mac.state_path/'sync-conflicts').glob('*.md'))
    (mac.vault_path/'facts/a.md').unlink()
    assert sync_once(mac,rpc)['downloaded']==1


def test_lost_response_replay_cas_and_cloud_task_authority(tmp_path):
    mac=config(tmp_path/'mac'); cloud=config(tmp_path/'cloud'); rpc=lambda r:handle(cloud,r)
    write(mac,'facts/a.md','content')
    lost=[False]
    def failing(r):
        value=rpc(r)
        if r['action']=='put' and not lost[0]: lost[0]=True; raise ConnectionError('lost ACK')
        return value
    with pytest.raises(ConnectionError): sync_once(mac,failing)
    assert sync_once(mac,rpc)['conflicts']==0
    assert handle(cloud,{'action':'put','path':'facts/a.md','base':'bad','text':'overwrite'})['conflict']
    cmd={'id':'phone1','action':'create','title':'research','body':'Need reply'}
    write(mac,'inbox/commands/1.json',json.dumps(cmd))
    sync_once(mac,rpc); sync_once(mac,rpc)
    assert tick(cloud)['prepared']==1
    sync_once(mac,rpc)
    views=list((mac.vault_path/'inbox/cloud-tasks').glob('*.md')); assert len(views)==1
    assert not (mac.state_path/'tasks.sqlite').exists()
    assert json.loads((mac.vault_path/'inbox/commands/1.receipt').read_text())['status']=='accepted'


@pytest.mark.parametrize('name',['../bad.md','/tmp/bad.md','.secret.md','inbox/tasks/a.md','inbox//commands/a.md','inbox/cloud-tasks/a.md'])
def test_protocol_rejects_escape_and_reserved(tmp_path,name):
    with pytest.raises(ValueError): note_path(tmp_path,name)


def test_symlink_and_private_state_never_sync(tmp_path):
    c=config(tmp_path/'c'); write(c,'ok.md','ok')
    (c.vault_path/'secret.md').symlink_to(tmp_path/'outside.md')
    (tmp_path/'outside.md').write_text('private')
    (c.state_path/'graph.token_cache.json').write_text('secret')
    assert handle(c,{'action':'manifest'})=={'notes':{'ok.md':sha('ok')}}
    with pytest.raises(ValueError): handle(c,{'action':'get','path':'secret.md'})
    with pytest.raises(ValueError): handle(c,{'action':'shell','command':'id'})


def test_command_directory_parent_symlink_not_transmitted(tmp_path):
    mac=config(tmp_path/'mac'); cloud=config(tmp_path/'cloud')
    outside=tmp_path/'outside'; (outside/'commands').mkdir(parents=True)
    (outside/'commands/private.json').write_text(json.dumps({'id':'x','action':'create','title':'secret','body':'secret'}))
    (mac.vault_path/'inbox').symlink_to(outside)
    assert sync_once(mac,lambda r:handle(cloud,r))['commands']==0
    assert handle(cloud,{'action':'tasks'})['tasks']==[]

def test_view_pagination_does_not_export_entire_vault(tmp_path,monkeypatch):
    from oneai.tasks import Tasks
    c=config(tmp_path)
    store=Tasks(c.state_path/'tasks.sqlite')
    ids={store.create(str(i),f'Task {i}','input') for i in range(105)}
    store.db.close()
    def forbidden(*args): raise AssertionError('Do not regenerate every task for each page')
    monkeypatch.setattr(Tasks,'export',forbidden)
    after=''; found=set(); sizes=[]
    while True:
        page=handle(c,{'action':'views','after':after})
        found.update(name[:-3] for name in page['views']); sizes.append(len(page['views']))
        if page['next'] is None: break
        after=page['next']
    assert found==ids and sizes==[50,50,5]
    with pytest.raises(ValueError): handle(c,{'action':'views','after':'../../secret'})
