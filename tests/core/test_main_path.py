"""Main-path regressions: real CLI, source versions, rule reloads and draft context."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from oneai.agent import save_manuscript
from oneai.config import Config
from oneai.context import load_context
from oneai.indexer import Index
from oneai.vault import read_note, resolve_note, select_lines, write_note


@pytest.fixture
def cfg(tmp_path):
    config = Config(tmp_path / 'vault', tmp_path / 'state', None)
    config.ensure_dirs()
    return config


def cli(cfg, *args, payload=None):
    env = {**os.environ, 'ONEAI_VAULT_PATH': str(cfg.vault_path),
           'ONEAI_STATE_PATH': str(cfg.state_path), 'DEEPSEEK_API_KEY': ''}
    return subprocess.run([sys.executable, '-m', 'oneai.cli', *args],
                          input=json.dumps(payload) if payload is not None else None,
                          text=True, capture_output=True, env=env)


def test_cli_refreshes_edits_deletes_and_renames(cfg):
    note = cfg.vault_path / 'note.md'
    note.write_text('oldkeyword')
    first = cli(cfg, 'search', 'oldkeyword', '--json')
    assert first.returncode == 0, first.stderr
    source = json.loads(first.stdout)[0]
    note.write_text('newkeyword')
    assert json.loads(cli(cfg, 'search', 'oldkeyword', '--json').stdout) == []
    assert json.loads(cli(cfg, 'search', 'newkeyword', '--json').stdout)
    note.rename(cfg.vault_path / 'renamed.md')
    assert json.loads(cli(cfg, 'search', 'newkeyword', '--json').stdout)[0]['path'] == 'renamed.md'
    (cfg.vault_path / 'renamed.md').unlink()
    assert json.loads(cli(cfg, 'search', 'newkeyword', '--json').stdout) == []
    old = cli(cfg, 'read', 'note.md', '--version', source['source_version'], '--lines', '1')
    assert old.returncode == 0, old.stderr
    assert json.loads(old.stdout)['body'] == 'oldkeyword'


def test_failed_rebuild_is_atomic(cfg, monkeypatch):
    a = cfg.vault_path / 'a.md'; b = cfg.vault_path / 'b.md'
    a.write_text('original'); b.write_text('second')
    index = Index(cfg.index_db); index.rebuild(cfg.vault_path)
    a.write_text('replacement')
    original = index._prepare
    def fail(vault, path):
        if path.name == 'b.md':
            raise OSError('simulated unavailable file')
        return original(vault, path)
    monkeypatch.setattr(index, '_prepare', fail)
    with pytest.raises(OSError):
        index.rebuild(cfg.vault_path)
    assert index.search('original')
    assert not index.search('replacement')
    index.close()


def test_history_survives_deleting_disposable_index(cfg):
    (cfg.vault_path / 'a.md').write_text('evidence')
    index = Index(cfg.index_db); index.sync(cfg.vault_path)
    version = index.search('evidence')[0].source_version
    index.close(); cfg.index_db.unlink(); (cfg.vault_path / 'a.md').unlink()
    index = Index(cfg.index_db)
    assert index.read_version(cfg.vault_path, 'a.md', version) == 'evidence'
    with pytest.raises(ValueError):
        index.read_version(cfg.vault_path, 'b.md', version)
    index.close()


@pytest.mark.parametrize('path', ['../outside.md', '/tmp/outside.md', 'facts/../../outside.md', '.env'])
def test_rejects_outside_paths(cfg, path):
    with pytest.raises(ValueError):
        resolve_note(cfg.vault_path, path)
    assert cli(cfg, 'read', path).returncode != 0


def test_symlink_escape_not_indexed_or_read(cfg, tmp_path):
    outside = tmp_path / 'outside.md'; outside.write_text('privatekeyword')
    (cfg.vault_path / 'escape.md').symlink_to(outside)
    with pytest.raises(ValueError):
        read_note(cfg.vault_path, 'escape.md')
    assert json.loads(cli(cfg, 'search', 'privatekeyword', '--json').stdout) == []


@pytest.mark.parametrize('lines', ['0-2', '-1-2', '3-1', '1-99', 'hello'])
def test_invalid_line_ranges(lines):
    with pytest.raises(ValueError):
        select_lines('one\ntwo', lines)


def test_title_is_retrievable_but_not_added_to_quoted_text(cfg):
    write_note(cfg.vault_path, 'a.md', {'title': '搜索标题'}, '正文证据')
    index = Index(cfg.index_db); index.sync(cfg.vault_path)
    result = index.search('搜索')[0]
    raw = index.read_version(cfg.vault_path, result.path, result.source_version)
    assert result.text == select_lines(raw, f'{result.start_line}-{result.end_line}')
    index.close()


def test_draft_save_preserves_current_agent_text_without_model(cfg, monkeypatch):
    monkeypatch.setattr('oneai.agent.LLM', lambda *_: pytest.fail('Must not call a second model'))
    body = '用户纠正：截止 10 月 1 日；活动开始时间未知。\n$HOME `echo no`'
    path = save_manuscript(cfg, '报名', body)
    note = read_note(cfg.vault_path, str(path.relative_to(cfg.vault_path)))
    assert note.body == body
    assert note.metadata['status'] == 'drafted'
    result = cli(cfg, 'draft-save', payload={'title': 'CLI', 'body': body})
    assert result.returncode == 0, result.stderr
    assert read_note(cfg.vault_path, json.loads(result.stdout)['path']).body == body


def test_draft_validates_historical_source(cfg):
    (cfg.vault_path / 'a.md').write_text('original evidence')
    index = Index(cfg.index_db); index.sync(cfg.vault_path)
    source = index.search('evidence')[0]; index.close()
    (cfg.vault_path / 'a.md').write_text('edited')
    path = save_manuscript(cfg, 'Draft', 'Based on original', sources=[
        {'path': source.path, 'version': source.source_version, 'lines': '1'}])
    note = read_note(cfg.vault_path, str(path.relative_to(cfg.vault_path)))
    assert note.metadata['sources'][0]['version'] == source.source_version
    with pytest.raises(ValueError):
        save_manuscript(cfg, 'Bad', 'Body', sources=[{'path': 'a.md', 'version': 'invented', 'lines': '1'}])


def test_rules_reload_in_new_process_and_record_versions(cfg):
    write_note(cfg.vault_path, 'rules/dates.md', {}, '截止不等于开始')
    first = json.loads(cli(cfg, 'context').stdout)['entries'][0]
    write_note(cfg.vault_path, 'rules/dates.md', {}, '开始时间未知时保留 unknown')
    second = json.loads(cli(cfg, 'context').stdout)['entries'][0]
    assert first['version'] != second['version']
    assert 'unknown' in second['text']
    path = save_manuscript(cfg, 'New task', 'Draft')
    assert read_note(cfg.vault_path, str(path.relative_to(cfg.vault_path))).metadata['context_versions_at_save'][0]['version'] == second['version']


def test_core_import_does_not_load_legacy_or_ui():
    result = subprocess.run([sys.executable, '-c',
        'import sys; import oneai.cli; import oneai.agent; '
        'assert not any(n.startswith(("oneai.legacy", "textual", "PIL")) for n in sys.modules)'],
        text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_watch_once_updates_index(cfg):
    (cfg.vault_path / 'a.md').write_text('watchedkeyword')
    assert cli(cfg, 'watch', '--once').returncode == 0
    index = Index(cfg.index_db)
    assert index.search('watchedkeyword')
    index.close()


def test_old_index_schema_is_rebuilt_on_search(cfg):
    import sqlite3
    conn = sqlite3.connect(cfg.index_db)
    conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(text,heading,path UNINDEXED,start_line UNINDEXED,end_line UNINDEXED,tokenize='trigram')")
    conn.execute("INSERT INTO chunks VALUES ('stale', '', 'deleted.md', 1, 1)")
    conn.commit(); conn.close()
    (cfg.vault_path / 'new.md').write_text('new evidence')
    result = cli(cfg, 'search', 'evidence', '--json')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]['source_version']
    assert json.loads(cli(cfg, 'search', 'stale', '--json').stdout) == []


def test_launcher_passes_extension_and_current_python(monkeypatch):
    from oneai.cli import main
    monkeypatch.setattr(sys, 'argv', ['oneai'])
    monkeypatch.delenv('ONEAI_PYTHON', raising=False)
    monkeypatch.setattr('shutil.which', lambda _: '/fake/pi')
    called = []
    def intercept(path, args):
        called.append((path, args))
        raise SystemExit(0)
    monkeypatch.setattr(os, 'execvp', intercept)
    with pytest.raises(SystemExit):
        main()
    assert called[0][1][1] == '--extension'
    assert Path(called[0][1][2]).is_file()
    assert os.environ['ONEAI_PYTHON'] == sys.executable


def test_repeated_sync_does_not_reindex_unchanged_files(cfg):
    (cfg.vault_path / 'a.md').write_text('unchanged')
    index = Index(cfg.index_db)
    assert index.sync(cfg.vault_path) == 1
    assert index.sync(cfg.vault_path) == 0
    assert index.sources.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 1
    (cfg.vault_path / 'a.md').unlink()
    assert index.index_file(cfg.vault_path, cfg.vault_path / 'a.md') == 0
    assert not index.search('unchanged')
    index.close()


def test_draft_rejects_invalid_source_type(cfg):
    with pytest.raises(ValueError):
        save_manuscript(cfg, 'Invalid', 'Body', sources={})
