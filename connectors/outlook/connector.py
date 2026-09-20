"""Outlook connector (Microsoft Graph, personal Microsoft accounts).

Flow per email — each message is handled exactly once:
  poll (delta query)
    → ledger.claim(message_id)          # idempotency gate; skips if seen
    → fetch thread history
    → draft manuscript via LLM + vault context
    → write vault/inbox/drafts/<id>.md with frontmatter status: drafted
  You edit the file and set `status: approved`.
  A separate pass sends approved drafts and marks the ledger `sent`.

Auth: MSAL device-code flow for personal Microsoft accounts. You must register
an app at https://portal.azure.com (Accounts in any org + personal accounts)
and set ONEAI_GRAPH_CLIENT_ID. Token cache lives in the state dir.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import msal
import urllib.request

from oneai.config import Config
from oneai.events import EventLog
from oneai.ledger import Ledger

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "Mail.Send"]


@dataclass
class Message:
    id: str
    subject: str
    sender: str
    received: str
    body_preview: str


class GraphAuth:
    def __init__(self, cfg: Config):
        self.client_id = os.environ.get("ONEAI_GRAPH_CLIENT_ID")
        if not self.client_id:
            raise RuntimeError(
                "ONEAI_GRAPH_CLIENT_ID is not set. Register an app in the Azure "
                "portal (personal Microsoft accounts supported) first."
            )
        self.cache_path = cfg.state_path / "graph.token_cache.json"
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self.cache.deserialize(self.cache_path.read_text())
        self.app = msal.PublicClientApplication(
            self.client_id,
            authority="https://login.microsoftonline.com/consumers",
            token_cache=self.cache,
        )

    def token(self) -> str:
        accounts = self.app.get_accounts()
        result = self.app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if not result:
            flow = self.app.initiate_device_flow(scopes=SCOPES)
            print(flow["message"])  # user signs in on another device/browser
            result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Graph auth failed: {result.get('error_description')}")
        self.cache_path.write_text(self.cache.serialize())
        return result["access_token"]


class OutlookClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.auth = GraphAuth(cfg)
        self.ledger = Ledger(cfg.ledger_db)
        self.events = EventLog(cfg.events_log)

    def _get(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.auth.token()}"})
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def poll_inbox(self, top: int = 20) -> list[Message]:
        """Fetch recent inbox messages, claiming each in the ledger exactly once."""
        data = self._get(
            f"{GRAPH}/me/mailFolders/inbox/messages"
            f"?$top={top}&$select=id,subject,from,receivedDateTime,bodyPreview"
            f"&$orderby=receivedDateTime desc"
        )
        fresh: list[Message] = []
        for m in data.get("value", []):
            mid = m["id"]
            if not self.ledger.claim("email", mid, status="seen"):
                continue  # already handled — never process twice
            self.events.emit("email.seen", message_id=mid, subject=m.get("subject", ""))
            fresh.append(
                Message(
                    id=mid,
                    subject=m.get("subject", ""),
                    sender=(m.get("from") or {}).get("emailAddress", {}).get("address", ""),
                    received=m.get("receivedDateTime", ""),
                    body_preview=m.get("bodyPreview", ""),
                )
            )
        return fresh

    # Drafting and sending are wired into the daemon in a later iteration:
    #   draft(message)  -> vault/inbox/drafts/<id>.md  (status: drafted)
    #   send_approved() -> POST /me/sendMail           (status: sent)
