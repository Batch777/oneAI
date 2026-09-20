"""Image display — pi-faithful Kitty graphics protocol implementation.

Ported from pi-tui's terminal-image.js:
- encodeKitty: a=T,f=100,q=2, C=1 (no cursor move), c/r cell sizing, m= chunking
- capability detection via env vars (Ghostty/Kitty/WezTerm → kitty), tmux → off
- cell-size calculation preserving aspect ratio (terminal cells are ~2x tall)
- text fallback: [Image: ~/path [mime] WxH]

Also: vault image-reference scanning so the agent can find images itself.
"""
from __future__ import annotations

import base64
import io
import os
import re
import sys
from pathlib import Path

IMG_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
CHUNK_SIZE = 4096
CELL_ASPECT = 0.5  # cell widthPx / heightPx heuristic (pi queries CSI 16t; we assume ~10x20)
MAX_COLS = 60
MAX_ROWS = 30


# --- capability detection (pi: detectCapabilitiesFromEnvironment) -----------

def supports_kitty() -> bool:
    if os.environ.get("TMUX") or os.environ.get("TERM", "").startswith(("tmux", "screen")):
        return False
    override = os.environ.get("ONEAI_IMAGE_PROTOCOL", "").lower()
    if override:
        return override == "kitty"
    term = (os.environ.get("TERM_PROGRAM", "") + " " + os.environ.get("TERM", "")).lower()
    return any(k in term for k in ("ghostty", "kitty", "wezterm")) or bool(
        os.environ.get("KITTY_WINDOW_ID") or os.environ.get("GHOSTTY_RESOURCES_DIR")
    )


# --- encoding (pi: encodeKitty) ----------------------------------------------

def _to_png(path: Path) -> bytes:
    from PIL import Image

    with Image.open(path) as im:
        dims = im.size
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    _last_dims[path] = dims
    return buf.getvalue()


_last_dims: dict[Path, tuple[int, int]] = {}


def calculate_cell_size(width_px: int, height_px: int, max_cols: int = MAX_COLS) -> tuple[int, int]:
    """Fit image into (columns, rows) preserving aspect ratio (pi: calculateImageCellSize)."""
    cols = max_cols
    rows = max(1, round(cols * (height_px / width_px) * CELL_ASPECT))
    if rows > MAX_ROWS:
        rows = MAX_ROWS
        cols = max(1, round(rows / (height_px / width_px) / CELL_ASPECT))
    return cols, rows


def encode_kitty(png: bytes, columns: int | None = None, rows: int | None = None,
                 image_id: int | None = None) -> str:
    """Kitty transmit+display sequence. C=1 keeps the cursor in place."""
    params = ["a=T", "f=100", "q=2", "C=1"]
    if columns:
        params.append(f"c={columns}")
    if rows:
        params.append(f"r={rows}")
    if image_id:
        params.append(f"i={image_id}")
    b64 = base64.standard_b64encode(png).decode()
    chunks = [b64[i : i + CHUNK_SIZE] for i in range(0, len(b64), CHUNK_SIZE)]
    out = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            out.append(f"\x1b_G{','.join(params)},m={1 if len(chunks) > 1 else 0};{chunk}\x1b\\")
        else:
            out.append(f"\x1b_Gm={1 if i < len(chunks) - 1 else 0};{chunk}\x1b\\")
    return "".join(out)


def fallback_text(path: Path, width_px: int = 0, height_px: int = 0) -> str:
    """pi: imageFallback — shown when the terminal can't render images."""
    display_path = str(path).replace(str(Path.home()), "~")
    dims = f" {width_px}x{height_px}" if width_px else ""
    return f"[Image: {display_path} [{path.suffix.lstrip('.')}] {dims}]"


def display(path: Path, out=None) -> str:
    """Emit the Kitty sequence at cursor; returns the fallback text if unsupported."""
    png = _to_png(path)
    w, h = _last_dims.get(path, (800, 600))
    if not supports_kitty():
        return fallback_text(path, w, h)
    cols, rows = calculate_cell_size(w, h)
    out = out or sys.stdout
    out.write(encode_kitty(png, columns=cols, rows=rows) + "\n" * rows)
    out.flush()
    return ""


# --- vault image references (lets the agent find images itself) ---------------

_IMG_REF = re.compile(r"!\[[^\]]*\]\(([^)]+)\)|([^\s!()\[\]]+\.(?:png|jpe?g|gif|webp|bmp))", re.IGNORECASE)


def find_image_references(vault: Path) -> list[dict]:
    """Scan vault notes for image references (markdown embeds or bare paths)."""
    found: list[dict] = []
    for md in sorted(vault.rglob("*.md")):
        for m in _IMG_REF.finditer(md.read_text(encoding="utf-8")):
            ref = m.group(1) or m.group(2)
            p = Path(ref).expanduser()
            if not p.is_absolute():
                p = vault / ref
            found.append({
                "note": str(md.relative_to(vault)),
                "ref": ref,
                "resolved": str(p),
                "exists": p.exists(),
            })
    return found
