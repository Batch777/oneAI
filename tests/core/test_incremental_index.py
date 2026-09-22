import os
import sqlite3
import time
from pathlib import Path
import pytest
from oneai.indexer import Index,HASH_AUDIT_SECONDS

@pytest.fixture
def setup(tmp_path):
    vault=tmp_path/'vault';vault.mkdir();(vault/'a.md').write_text('original evidence')
    index=Index(tmp_path/'index.sqlite');index.sync(vault)
    yield vault,index
    index.close()

def test_unchanged_files_never_read_or_rechunk(setup,monkeypatch):
    vault,index=setup
    monkeypatch.setattr(index,'_prepare',lambda *_:pytest.fail('unchanged content read'))
    assert index.sync(vault)==0
    assert index.last_sync_stats['hashed']==0 and index.last_sync_stats['skipped']==1

def test_same_size_and_restored_mtime_edit_is_detected(setup):
    vault,index=setup;p=vault/'a.md';before=p.stat()
    p.write_text('modified evidence');os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns))
    assert index.sync(vault)>0
    assert index.search('modified') and not index.search('original')

def test_touch_hashes_without_rewriting_chunks(setup):
    vault,index=setup;p=vault/'a.md'
    os.utime(p,ns=(p.stat().st_atime_ns,p.stat().st_mtime_ns+1000000))
    assert index.sync(vault)==0 and index.last_sync_stats['hashed']==1

def test_rename_delete_and_historical_citation(setup):
    vault,index=setup;version=index.search('original')[0].source_version
    (vault/'a.md').rename(vault/'b.md');index.sync(vault)
    assert index.search('original')[0].path=='b.md'
    assert index.read_version(vault,'a.md',version)=='original evidence'
    (vault/'b.md').unlink();index.sync(vault)
    assert not index.search('original')
    assert index.conn.execute('SELECT count(*) FROM file_state').fetchone()[0]==0

def test_periodic_audit_and_force_recheck(setup):
    vault,index=setup
    with index.conn:index.conn.execute('UPDATE file_state SET verified_at=?',(time.time()-HASH_AUDIT_SECONDS-1,))
    assert index.sync(vault)==0 and index.last_sync_stats['hashed']==1
    assert index.rebuild(vault)>0 and index.last_sync_stats['hashed']==1

def test_old_index_without_manifest_is_migrated(setup):
    vault,index=setup
    with index.conn:index.conn.execute('DELETE FROM file_state')
    assert index.sync(vault)==0 and index.last_sync_stats['hashed']==1
    index.sync(vault);assert index.last_sync_stats['skipped']==1

def test_generated_folders_are_pruned_before_walk(setup,monkeypatch):
    vault,index=setup;folder=vault/'inbox/tasks';folder.mkdir(parents=True)
    (folder/'invalid.md').write_bytes(b'\xff')
    assert index.sync(vault)==0 and index.last_sync_stats['checked']==1

def test_mid_read_changes_roll_back_manifest_and_index(setup,monkeypatch):
    vault,index=setup;original=index._prepare
    with index.conn:index.conn.execute('UPDATE file_state SET verified_at=0')
    def racing(root,path):
        value=original(root,path);path.write_text(path.read_text()+'changed');return value
    monkeypatch.setattr(index,'_prepare',racing)
    with pytest.raises(OSError):index.sync(vault)
    assert index.search('original') and not index.search('changed')
    assert index.conn.execute('SELECT verified_at FROM file_state').fetchone()[0]==0

def test_unavailable_tree_does_not_delete_existing_index(setup,monkeypatch):
    vault,index=setup
    def denied(*args,**kwargs):kwargs['onerror'](PermissionError('unavailable'));yield
    monkeypatch.setattr(os,'walk',denied)
    with pytest.raises(PermissionError):index.sync(vault)
    assert index.search('original')
