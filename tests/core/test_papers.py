from pathlib import Path
from oneai.config import Config
from oneai.papers import import_papers
from oneai.indexer import Index

def test_import_deduplicates_preserves_original_and_retires_old_text(tmp_path):
    root=tmp_path/'desktop';root.mkdir()
    a=root/'FlashGS.pdf';b=root/'copy.pdf';a.write_bytes(b'PDF v1');b.write_bytes(a.read_bytes())
    cfg=Config(tmp_path/'vault',tmp_path/'state',None)
    def parse(path,version,cache):return ['first page','two-step prefetching evidence '+path.read_text()], 'test-parser'
    first=import_papers(root,cfg,tmp_path/'originals',parse=parse)
    assert first['unique_documents']==1 and first['pages']==2
    assert len(list((tmp_path/'originals').glob('*.pdf')))==1
    idx=Index(cfg.index_db);idx.sync(cfg.vault_path)
    hit=idx.search('prefetching')[0];citation=hit.citation
    assert 'PDF 第 2 页' in hit.heading
    again=import_papers(root,cfg,tmp_path/'originals',parse=parse)
    assert again['unique_documents']==1 and a.read_bytes()==b'PDF v1'
    b.unlink();a.write_bytes(b'PDF v2')
    third=import_papers(root,cfg,tmp_path/'originals',parse=parse)
    idx.sync(cfg.vault_path)
    assert len(list(Path(third['vault_folder']).glob('*.md')))==1
    assert len(list((tmp_path/'originals').glob('*.pdf')))==2
    assert 'PDF v1' in idx.read_version(cfg.vault_path,hit.path,hit.source_version)
    idx.close()

def test_one_bad_pdf_does_not_block_others(tmp_path):
    root=tmp_path/'desktop';root.mkdir()
    (root/'bad.pdf').write_bytes(b'bad');(root/'good.pdf').write_bytes(b'good')
    cfg=Config(tmp_path/'vault',tmp_path/'state',None)
    def parse(path,version,cache):
        if path.read_bytes()==b'bad':raise ValueError('broken PDF')
        return ['searchable page'], 'test-parser'
    result=import_papers(root,cfg,tmp_path/'originals',parse=parse)
    assert result['unique_documents']==1
    assert len(result['errors'])==1
