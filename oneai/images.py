"""Image helpers: Kitty graphics protocol display + base64 data URLs for the LLM.

Kitty protocol is supported by Ghostty/Kitty/WezTerm (same approach as pi-tui's
Image component). Falls back to macOS `open` elsewhere.
"""
from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _to_png(path: Path) -> bytes:
    """Load any common image format and return PNG bytes (f=100)."""
    from PIL import Image

    with Image.open(path) as im:
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def kitty_sequence(png: bytes) -> str:
    """Build a Kitty graphics transmit+display sequence (chunked base64)."""
    b64 = base64.standard_b64encode(png).decode()
    chunks = [b64[i : i + 4096] for i in range(0, len(b64), 4096)]
    parts = []
    for i, chunk in enumerate(chunks):
        m = 1 if i < len(chunks) - 1 else 0
        ctl = "a=T,f=100,q=2" if i == 0 else f"m={m}"
        if i == 0:
            ctl += f",m={m}"
        parts.append(f"\x1b_G{ctl};{chunk}\x1b\\")
    return "".join(parts)


def display(path: Path, out=None) -> None:
    """Display an image at the cursor via Kitty protocol."""
    out = out or sys.stdout
    out.write(kitty_sequence(_to_png(path)))
    out.write("\n")
    out.flush()


def as_data_url(path: Path) -> str:
    """base64 data URL for OpenAI-compatible vision APIs."""
    ext = path.suffix.lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return f"data:{mime};base64,{data}"


def supports_kitty() -> bool:
    term = os.environ.get("TERM_PROGRAM", "") + os.environ.get("TERM", "")
    return any(k in term.lower() for k in ("ghostty", "kitty", "wezterm"))
