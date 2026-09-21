"""Archive tests must not accidentally operate on the personal vault."""
import os
import pytest

@pytest.fixture(autouse=True)
def isolated_legacy_environment(tmp_path, monkeypatch, request):
    if request.node.path.name == 'test_live.py' and os.environ.get('ONEAI_LIVE_TEST') == '1':
        return  # explicitly opted-in live test, documented in that module
    monkeypatch.setenv('ONEAI_VAULT_PATH', str(tmp_path / 'vault'))
    monkeypatch.setenv('ONEAI_STATE_PATH', str(tmp_path / 'state'))
    monkeypatch.setenv('DEEPSEEK_API_KEY', '')
    from oneai.config import Config
    from oneai.vault import init_vault
    cfg = Config.load(); cfg.ensure_dirs(); init_vault(cfg.vault_path)
