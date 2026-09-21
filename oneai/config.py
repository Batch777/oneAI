"""Central configuration.

Portability rules:
- Every path is overridable via environment variables, so the same code runs
  on macOS (iCloud-synced vault) and a headless Linux server (plain directory).
- No macOS-specific APIs anywhere in oneai/ — platform glue lives in connectors/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_vault_path() -> Path:
    """iCloud Drive on macOS (visible in iPhone Files app); ~/oneAI/vault elsewhere."""
    icloud = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
    base = icloud if icloud.exists() else Path.home()
    return base / "oneAI" / "vault"


@dataclass
class Config:
    vault_path: Path
    state_path: Path
    deepseek_api_key: str | None
    deepseek_base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"

    def __post_init__(self) -> None:
        self.vault_path = self.vault_path.expanduser().resolve()
        self.state_path = self.state_path.expanduser().resolve()

    @classmethod
    def load(cls) -> "Config":
        return cls(
            vault_path=Path(os.environ.get("ONEAI_VAULT_PATH", _default_vault_path())).expanduser(),
            state_path=Path(os.environ.get("ONEAI_STATE_PATH", "~/.oneai/state")).expanduser(),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.environ.get("ONEAI_MODEL", "deepseek-flash"),
        )

    def ensure_dirs(self) -> None:
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self.state_path.mkdir(parents=True, exist_ok=True)

    @property
    def index_db(self) -> Path:
        return self.state_path / "index.sqlite"

    @property
    def ledger_db(self) -> Path:
        return self.state_path / "ledger.sqlite"

    @property
    def events_log(self) -> Path:
        return self.state_path / "events.jsonl"
