"""LLM client: OpenAI-compatible API pointed at DeepSeek."""
from __future__ import annotations

from openai import OpenAI

from .config import Config


class LLM:
    def __init__(self, cfg: Config):
        if not cfg.deepseek_api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not set")
        self.client = OpenAI(api_key=cfg.deepseek_api_key, base_url=cfg.deepseek_base_url)
        self.model = cfg.model

    def chat(self, system: str, user: str) -> str:
        # Note: no temperature param — DeepSeek reasoning models reject it.
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
