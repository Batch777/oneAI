"""Unit tests: vault + indexer (chunking, citations, CJK, exclusions)."""
from __future__ import annotations

import pytest

from oneai.indexer import Index, chunk_markdown, _cjk_bigrams, _frontmatter_title
from oneai.vault import init_vault, read_note, write_note


@pytest.fixture()
def vault(tmp_path):
    v = tmp_path / "vault"
    init_vault(v)
    return v


@pytest.fixture()
def index(tmp_path):
    idx = Index(tmp_path / "index.sqlite")
    yield idx
    idx.close()


class TestVault:
    def test_init_creates_skeleton(self, vault):
        for d in ("people", "projects", "facts", "decisions", "journal", "inbox"):
            assert (vault / d).is_dir()
        assert (vault / "README.md").exists()

    def test_init_idempotent(self, vault):
        init_vault(vault)  # second run must not raise or clobber
        assert (vault / "README.md").exists()

    def test_write_read_roundtrip(self, vault):
        write_note(vault, "facts/test.md", {"id": "t1", "title": "测试"}, "正文内容")
        note = read_note(vault, "facts/test.md")
        assert note.metadata["id"] == "t1"
        assert "正文内容" in note.body
        assert note.title == "测试"


class TestChunking:
    def test_heading_split(self):
        # sections big enough to stand alone keep their own headings
        chunks = chunk_markdown("# A\n\n" + "a" * 100 + "\n\n## B\n\n" + "b" * 100)
        headings = [c[2] for c in chunks]
        assert "A" in headings and "B" in headings

    def test_lone_heading_merges_with_next(self):
        chunks = chunk_markdown("# Title\n\n## Section\n\n" + "x" * 300)
        # "# Title" (tiny) must merge forward, not stand alone
        assert not any(len(c[3]) < 60 and c[3].strip() == "# Title" for c in chunks)

    def test_short_section_keeps_own_heading(self):
        # regression: 200-char threshold merged real sections under wrong headings
        text = "## 硕士\n\n" + "内容 " * 40 + "\n\n## 本科\n\n" + "内容 " * 40
        chunks = chunk_markdown(text)
        headings = [c[2] for c in chunks]
        assert "硕士" in headings and "本科" in headings

    def test_frontmatter_title_extracted(self):
        assert _frontmatter_title('---\ntitle: "教育经历"\n---\nbody') == "教育经历"
        assert _frontmatter_title("no frontmatter") == ""

    def test_cjk_bigrams(self):
        assert _cjk_bigrams("用户是谁") == ["用户", "户是", "是谁"]
        assert _cjk_bigrams("english only") == []


class TestIndex:
    def _note(self, vault, rel, title, body):
        write_note(vault, rel, {"id": rel, "title": title, "created": "2026-01-01"}, body)

    def test_search_returns_citation_with_lines(self, vault, index):
        self._note(vault, "facts/coffee.md", "咖啡", "# 咖啡\n\n喜欢手冲，中烘。\n")
        index.rebuild(vault)
        (r,) = index.search("手冲")
        assert r.path == "facts/coffee.md"
        assert r.start_line >= 1 and r.end_line >= r.start_line
        assert r.citation.startswith("facts/coffee.md#L")
        assert "手冲" in r.text

    def test_cjk_fallback_matches_two_char_words(self, vault, index):
        # trigram FTS cannot match 2-char words; the LIKE fallback must
        self._note(vault, "facts/id.md", "身份", "# 身份\n\n姓名：测试用户\n")
        index.rebuild(vault)
        assert index.search("用户"), "CJK bigram fallback failed"

    def test_drafts_excluded(self, vault, index):
        self._note(vault, "inbox/drafts/d1.md", "手稿", "# 手稿\n\n这是一份手稿内容\n")
        self._note(vault, "facts/real.md", "真实", "# 真实\n\n这是真实笔记\n")
        index.rebuild(vault)
        results = index.search("手稿")
        assert all("inbox/drafts" not in r.path for r in results)

    def test_frontmatter_title_searchable(self, vault, index):
        self._note(vault, "facts/edu.md", "教育经历", "# 正文\n\n本科：某大学\n")
        index.rebuild(vault)
        assert any(r.path == "facts/edu.md" for r in index.search("教育"))

    def test_rebuild_resets(self, vault, index):
        self._note(vault, "facts/a.md", "A", "# A\n\n内容甲\n")
        index.rebuild(vault)
        assert index.search("内容甲")
        (vault / "facts/a.md").unlink()
        index.rebuild(vault)
        assert not index.search("内容甲")

    def test_free_text_query_does_not_crash(self, vault, index):
        self._note(vault, "facts/a.md", "A", "# A\n\nanything\n")
        index.rebuild(vault)
        index.search("what about special chars? !@#$%^&*()")  # must not raise
