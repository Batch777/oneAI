"""Agent: answer questions from the vault, always with provenance citations."""
from __future__ import annotations

from .config import Config
from .events import EventLog
from .indexer import Index, SearchResult
from .llm import LLM

SYSTEM = """You are a personal assistant answering questions about the user's life.
Use ONLY the context notes below. Rules:
- Cite every claim with its source in the form [[path#Lstart-Lend]].
- If the context is insufficient, say what is missing instead of guessing.
- Reply in the user's preferred language (see identity note if present).
- Be concise."""


def _identity_context(cfg: Config) -> str:
    """Load facts/identity.md so the assistant knows who it serves.
    Personal data lives in the vault, never in the codebase."""
    path = cfg.vault_path / "facts" / "identity.md"
    if not path.exists():
        return ""
    body = path.read_text(encoding="utf-8")
    return f"\n\nIdentity of the user you serve:\n{body}"


def build_context(results: list[SearchResult]) -> str:
    blocks = []
    for r in results:
        blocks.append(f"--- {r.citation} ({r.heading or 'no heading'}) ---\n{r.snippet}")
    return "\n\n".join(blocks)


def ask(cfg: Config, question: str, k: int = 8) -> str:
    index = Index(cfg.index_db)
    results = index.search(question, k=k)
    index.close()
    log = EventLog(cfg.events_log)

    if not results:
        log.emit("ask", question=question, hits=0)
        return "No relevant notes found in the vault. Try `oneai index` first, or add notes."

    answer = LLM(cfg).chat(SYSTEM + _identity_context(cfg),
                           f"Context:\n{build_context(results)}\n\nQuestion: {question}")
    log.emit("ask", question=question, hits=len(results),
             sources=[r.citation for r in results])

    sources = "\n".join(f"- {r.citation}" for r in results)
    return f"{answer}\n\nSources:\n{sources}"
