from pathlib import Path


def test_web_and_worker_share_authoritative_state_configuration():
    root=Path(__file__).resolve().parents[2]
    web=(root/'deploy/systemd/oneai-web.service').read_text()
    worker=(root/'deploy/systemd/oneai-worker.service').read_text()
    environment='EnvironmentFile=/etc/oneai/outlook.env'
    assert environment in web and environment in worker
    assert 'Environment=ONEAI_STATE_PATH=' not in web
    assert 'Environment=ONEAI_VAULT_PATH=' not in web
