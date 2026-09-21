import importlib.util
import json
from pathlib import Path
import sys
import types

spec = importlib.util.spec_from_file_location('paper_pilot', Path(__file__).parents[2] / 'scripts/paper_pilot.py')
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_pdf_baseline_dedup_page_citation_and_font_repair(tmp_path, monkeypatch):
    class Page:
        def extract_text(self): return '研究高斯渲染 two-step prefetching \ud835'
    class Reader:
        def __init__(self, path): self.pages = [Page(), Page()]
    monkeypatch.setitem(sys.modules, 'pypdf', types.SimpleNamespace(PdfReader=Reader))
    root, output = tmp_path / 'pdfs', tmp_path / 'index'
    root.mkdir()
    (root / 'a.pdf').write_bytes(b'same')
    (root / 'b.pdf').write_bytes(b'same')
    summary = pilot.build(root, output)
    assert summary['unique_documents'] == 1
    assert summary['indexed_pages'] == 2
    result = pilot.search(output, '高斯渲染')
    assert {int(hit['page']) for hit in result} == {1, 2}
    assert all('@' in hit['citation'] and '#page=' in hit['citation'] for hit in result)
    assert json.loads((output / 'baseline.json').read_text())['files'][0]['encoding_repaired']
    (root / 'a.pdf').unlink()
    (root / 'b.pdf').unlink()
    assert pilot.build(root, output)['indexed_pages'] == 0
    assert pilot.search(output, 'prefetching') == []
