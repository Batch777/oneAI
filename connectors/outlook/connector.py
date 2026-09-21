"""Read-only personal Outlook ingestion with resumable, transactional delta pages.

Run `python -m connectors.outlook.connector auth` interactively once, then `sync`
from a timer. No mail sending, LLM processing or mobile transport is implied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import urllib.request
from urllib.parse import urlparse

from oneai.config import Config

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read"]
INITIAL = GRAPH + "/me/mailFolders/inbox/messages/delta?$select=id,subject,from,receivedDateTime,bodyPreview,body,internetMessageId"


class NeedsAuthorization(RuntimeError):
    pass


def private_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class GraphAuth:
    def __init__(self, cfg: Config):
        import msal
        self.client_id = os.environ.get("ONEAI_GRAPH_CLIENT_ID")
        if not self.client_id:
            raise NeedsAuthorization("Set ONEAI_GRAPH_CLIENT_ID to your personal-account app registration.")
        self.cache_path = cfg.state_path / "graph.token_cache.json"
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self.cache.deserialize(self.cache_path.read_text())
        self.app = msal.PublicClientApplication(self.client_id, authority="https://login.microsoftonline.com/consumers", token_cache=self.cache)

    def account(self) -> dict:
        accounts = self.app.get_accounts()
        if len(accounts) != 1:
            raise NeedsAuthorization("Expected exactly one authorized account; run auth with a dedicated state directory.")
        return accounts[0]

    def authorize(self) -> None:
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise NeedsAuthorization("Could not start device authorization.")
        print(flow["message"], flush=True)
        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise NeedsAuthorization("Authorization did not complete.")
        private_write(self.cache_path, self.cache.serialize())
        self.account()

    def token(self) -> str:
        result = self.app.acquire_token_silent(SCOPES, account=self.account())
        if self.cache.has_state_changed:
            private_write(self.cache_path, self.cache.serialize())
        if not result or "access_token" not in result:
            raise NeedsAuthorization("waiting_auth: run auth interactively; background sync never prompts.")
        return result["access_token"]

    @property
    def identity(self) -> str:
        return hashlib.sha256((self.client_id + ':' + self.account()["home_account_id"]).encode()).hexdigest()


class DeltaStore:
    def __init__(self, path: Path, identity: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,payload TEXT NOT NULL,deleted INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY,message_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending');
        """)
        row = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
        if row and row[0] != identity:
            self.db.close()
            raise ValueError("Mailbox identity changed; use a separate state directory.")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (identity,))

    def cursor(self) -> str:
        row = self.db.execute("SELECT value FROM metadata WHERE key='cursor'").fetchone()
        return row[0] if row else INITIAL

    def apply(self, page: dict) -> tuple[str, bool]:
        cursor = page.get("@odata.nextLink") or page.get("@odata.deltaLink")
        if not cursor or not isinstance(page.get("value"), list):
            raise ValueError("Malformed delta page: refusing to advance cursor")
        validate_url(cursor)
        with self.db:
            for message in page["value"]:
                mid = message["id"]
                previous = self.db.execute("SELECT payload FROM messages WHERE id=?", (mid,)).fetchone()
                merged = json.loads(previous[0]) if previous else {}
                merged.pop("@removed", None)
                merged.update(message)
                payload = json.dumps(merged, sort_keys=True, ensure_ascii=False)
                deleted = int("@removed" in message)
                self.db.execute("INSERT INTO messages VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,deleted=excluded.deleted", (mid, payload, deleted))
                # Persist a versioned event in the SAME transaction as the cursor.
                # Processing this queue is a separate worker; ingest is not 'done'.
                key = hashlib.sha256((mid + '\0' + payload).encode()).hexdigest()
                self.db.execute("INSERT OR IGNORE INTO work(id,message_id,payload) VALUES (?,?,?)", (key, mid, payload))
            self.db.execute("INSERT INTO metadata VALUES ('cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (cursor,))
        return cursor, "@odata.nextLink" in page


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
        raise ValueError("Refusing to send mailbox credentials to an unexpected URL")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Unexpected Graph redirect")


class OutlookClient:
    def __init__(self, cfg: Config):
        self.auth = GraphAuth(cfg)
        self.store = DeltaStore(cfg.state_path / "outlook.sqlite", self.auth.identity)

    def _get(self, url: str) -> dict:
        validate_url(url)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.auth.token()}", "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"'})
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            return json.load(response)

    def sync(self, max_pages: int = 100) -> dict:
        cursor = self.store.cursor()
        for number in range(1, max_pages + 1):
            cursor, more = self.store.apply(self._get(cursor))
            if not more:
                return {"pages": number, "caught_up": True}
        return {"pages": max_pages, "caught_up": False}


def main() -> None:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("command", choices=["auth", "sync"])
    args = parser.parse_args()
    cfg = Config.load()
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    # A timer and an interactive login must not rewrite the same token cache.
    import fcntl
    with (cfg.state_path / "outlook.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if args.command == "auth":
                GraphAuth(cfg).authorize()
                print("Read-only mailbox authorization saved.")
            else:
                client = OutlookClient(cfg)
                try:
                    print(json.dumps(client.sync()))
                finally:
                    client.store.db.close()
        except NeedsAuthorization as error:
            parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
