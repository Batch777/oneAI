import json
import os
import pytest
from connectors.outlook.connector import DeltaStore, INITIAL, SCOPES, private_write, validate_url

NEXT = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$skiptoken=opaque'
FINAL = 'https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=opaque'


def test_delta_resume_replay_and_removed(tmp_path):
    path = tmp_path / 'mail.sqlite'
    store = DeltaStore(path, 'account-a')
    page = {'value': [{'id': '1', 'subject': 'hello'}], '@odata.nextLink': NEXT}
    assert store.cursor() == INITIAL
    assert store.apply(page) == (NEXT, True)
    store.apply(page)
    assert store.db.execute('SELECT COUNT(*) FROM work').fetchone()[0] == 1
    store.db.close()
    store = DeltaStore(path, 'account-a')
    assert store.cursor() == NEXT
    store.apply({'value': [{'id': '1', '@removed': {'reason': 'deleted'}}], '@odata.deltaLink': FINAL})
    assert store.db.execute('SELECT deleted FROM messages').fetchone()[0] == 1
    assert store.cursor() == FINAL
    store.db.close()


def test_invalid_page_rolls_back_messages_and_cursor(tmp_path):
    store = DeltaStore(tmp_path / 'mail.sqlite', 'a')
    with pytest.raises(KeyError):
        store.apply({'value': [{'id': '1'}, {}], '@odata.nextLink': NEXT})
    assert store.cursor() == INITIAL
    assert store.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM work').fetchone()[0] == 0
    store.db.close()


def test_mailbox_binding(tmp_path):
    path = tmp_path / 'mail.sqlite'
    DeltaStore(path, 'a').db.close()
    with pytest.raises(ValueError, match='identity'):
        DeltaStore(path, 'b')


@pytest.mark.parametrize('url', ['http://graph.microsoft.com/v1.0/me', 'https://evil.test/v1.0/me', 'https://graph.microsoft.com@evil.test/v1.0/me', 'https://graph.microsoft.com/other'])
def test_credentials_never_follow_foreign_cursor(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_permissions_and_private_token_file(tmp_path):
    assert SCOPES == ['Mail.Read']
    path = tmp_path / 'tokens'
    private_write(path, 'first')
    private_write(path, 'second')
    assert path.read_text() == 'second'
    assert path.stat().st_mode & 0o777 == 0o600


def test_partial_delta_keeps_prior_fields(tmp_path):
    store = DeltaStore(tmp_path / 'mail.sqlite', 'a')
    store.apply({'value': [{'id': '1', 'subject': 'before', 'body': {'content': 'retained'}}], '@odata.deltaLink': FINAL})
    store.apply({'value': [{'id': '1', 'subject': 'after'}], '@odata.deltaLink': FINAL})
    payload = json.loads(store.db.execute('SELECT payload FROM messages').fetchone()[0])
    assert payload['subject'] == 'after'
    assert payload['body']['content'] == 'retained'
    store.db.close()


def test_background_token_cannot_start_device_login():
    from connectors.outlook.connector import GraphAuth, NeedsAuthorization
    class App:
        def get_accounts(self): return [{'home_account_id': 'a'}]
        def acquire_token_silent(self, *args, **kwargs): return None
        def initiate_device_flow(self, *args, **kwargs): raise AssertionError('Background prompted')
    class Cache:
        has_state_changed = False
    auth = GraphAuth.__new__(GraphAuth)
    auth.app, auth.cache = App(), Cache()
    with pytest.raises(NeedsAuthorization, match='waiting_auth'):
        auth.token()
