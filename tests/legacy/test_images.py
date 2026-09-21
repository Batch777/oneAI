"""Unit tests: images (Kitty encoding, sizing, vault image discovery)."""
from __future__ import annotations

import base64

import pytest
from PIL import Image

from oneai.legacy.images import (
    calculate_cell_size,
    encode_kitty,
    fallback_text,
    find_image_references,
    supports_kitty,
    MAX_ROWS,
)


@pytest.fixture()
def png_bytes():
    import io

    buf = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


class TestKittyEncoding:
    def test_params(self, png_bytes):
        seq = encode_kitty(png_bytes, columns=40, rows=10, image_id=7)
        assert "a=T" in seq and "f=100" in seq and "q=2" in seq
        assert "C=1" in seq  # don't move cursor (pi behavior)
        assert "c=40" in seq and "r=10" in seq and "i=7" in seq

    def test_chunking(self, png_bytes):
        # 4096-char base64 chunks; a 400x200 PNG must produce several
        seq = encode_kitty(png_bytes)
        chunks = seq.count("\x1b_G")
        assert chunks >= 1
        # continuation flags: first chunk carries params, middle m=1, last m=0
        assert "m=0;" in seq

    def test_valid_base64_payload(self, png_bytes):
        seq = encode_kitty(png_bytes)
        payload = "".join(
            part.split(";")[1] for part in
            [c.rstrip("\x1b\\") for c in seq.split("\x1b_G") if c]
        )
        assert base64.standard_b64decode(payload) == png_bytes


class TestSizing:
    def test_landscape(self):
        cols, rows = calculate_cell_size(1600, 900)
        assert rows < cols  # landscape → wider than tall

    def test_portrait_capped(self):
        cols, rows = calculate_cell_size(800, 4000)
        assert rows == MAX_ROWS
        assert cols >= 1

    def test_aspect_ratio(self):
        cols, rows = calculate_cell_size(1000, 1000)
        assert rows == round(cols * 0.5)  # square image, cells 2x tall


class TestDiscovery:
    def test_markdown_and_bare_refs(self, tmp_path):
        img = tmp_path / "photo.jpg"
        Image.new("RGB", (10, 10)).save(img)
        (tmp_path / "note.md").write_text(
            f"![照片]({img})\n裸路径 {img} 重复出现\n", encoding="utf-8")
        refs = find_image_references(tmp_path)
        assert refs and all(r["exists"] for r in refs)
        # markdown ref must not include the "![" prefix (regex regression)
        assert all("![" not in r["ref"] for r in refs)

    def test_missing_file_flagged(self, tmp_path):
        (tmp_path / "n.md").write_text("![x](/nonexistent/pic.png)", encoding="utf-8")
        (r,) = find_image_references(tmp_path)
        assert r["exists"] is False


class TestFallback:
    def test_fallback_text(self):
        t = fallback_text(__import__("pathlib").Path("/tmp/a.png"), 100, 50)
        assert "[Image:" in t and "100x50" in t

    def test_capability_env(self, monkeypatch):
        monkeypatch.setenv("TERM_PROGRAM", "ghostty")
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("ONEAI_IMAGE_PROTOCOL", raising=False)
        assert supports_kitty() is True
        monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
        assert supports_kitty() is False  # tmux disables (pi behavior)
        monkeypatch.delenv("TMUX")
        monkeypatch.setenv("ONEAI_IMAGE_PROTOCOL", "none")
        assert supports_kitty() is False  # explicit override
