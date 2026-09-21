"""Import local PDFs into versioned originals plus searchable Markdown.

Original files are never moved. Heavy parsing runs on the Mac; the relay only
indexes Markdown. A verified pilot cache can avoid parsing identical bytes again.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from .config import Config
from .vault import atomic_write


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pages_for(path, version, cache=None):
    if cache and cache.is_file():
        with sqlite3.connect(f'file:{cache.resolve()}?mode=ro', uri=True) as db:
            rows = db.execute('SELECT page,text FROM pages WHERE version=? ORDER BY CAST(page AS INTEGER)', (version,)).fetchall()
        if rows and [int(r[0]) for r in rows] == list(range(1,len(rows)+1)):
            return [r[1] for r in rows], 'pypdf-pilot-cache'
    import pypdf
    reader = pypdf.PdfReader(path)
    return [(page.extract_text() or '').replace('\x00','').encode('utf-8','replace').decode('utf-8') for page in reader.pages], 'pypdf-'+pypdf.__version__


def import_papers(root, cfg, originals, cache=None, parse=pages_for):
    root, originals = Path(root).resolve(strict=True), Path(originals).resolve()
    if not root.is_dir():
        raise ValueError('PDF source must be a directory')
    cfg.ensure_dirs(); originals.mkdir(parents=True,exist_ok=True)
    dataset = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    folder = cfg.vault_path/'papers'/'imported'/dataset
    folder.mkdir(parents=True,exist_ok=True)
    manifest_path = cfg.state_path/'paper-imports'/(dataset+'.json')
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else {'files':[]}
    active, files, errors, seen, byte_count, page_count = set(), [], [], set(), 0, 0
    for source in sorted(root.rglob('*')):
        if source.suffix.lower() != '.pdf' or not source.is_file():
            continue
        relative = str(source.relative_to(root))
        try:
            if source.is_symlink() or not source.resolve().is_relative_to(root):
                raise ValueError('PDF symlinks are not imported')
            version = sha(source)
            if version in seen:
                files.append({'source':relative,'version':version,'duplicate':True})
                continue
            # Parse a private snapshot, so references and extracted text always
            # describe the same bytes even if the source changes during import.
            with tempfile.TemporaryDirectory(prefix='oneai-paper-') as tmp:
                snapshot = Path(tmp)/'source.pdf'
                shutil.copyfile(source,snapshot)
                if sha(snapshot) != version:
                    raise ValueError('Source changed during import; retry later')
                pages, parser = parse(snapshot, version, cache)
                if not pages:
                    raise ValueError('PDF contains no pages')
                target = originals/(version+'.pdf')
                if target.exists():
                    if target.is_symlink() or sha(target)!=version:
                        raise ValueError('Archived PDF hash mismatch')
                else:
                    with tempfile.NamedTemporaryFile(dir=originals,prefix='.import-',delete=False) as stream:
                        staging=Path(stream.name)
                    try:
                        shutil.copyfile(snapshot,staging)
                        staging.chmod(0o600)
                        staging.replace(target)
                    finally:
                        staging.unlink(missing_ok=True)
            title = source.stem.replace('\n',' ')
            header = '\n'.join(['---','title: '+json.dumps(title,ensure_ascii=False),'kind: paper_extracted','pdf_sha256: '+version,
                'pdf_file: '+json.dumps(target.name),'parser: '+parser,'---',
                '# '+title,'','> PDF 提取文本，仅作资料。页码是 PDF 文件的物理页序号；公式、图表和阅读顺序需核对原件。',''])
            parts, current = [], header
            for number,text in enumerate(pages,1):
                block=f'\n## {title} · PDF 第 {number} 页\n\n'+(text.strip() or '[此页未提取到文字，需要查看原件或进行 OCR。]')+'\n'
                if len(block.encode())>1_800_000:
                    raise ValueError('Extracted page exceeds synchronization limit')
                if len((current+block).encode())>1_800_000:
                    parts.append(current);current=header
                current+=block
            parts.append(current)
            names=[]
            for i,body in enumerate(parts,1):
                note=folder/(version+f'-{i:03d}.md')
                if not note.exists() or note.read_text()!=body:
                    atomic_write(note,body)
                active.add(note.name); names.append(note.name)
            seen.add(version);byte_count+=source.stat().st_size;page_count+=len(pages)
            files.append({'source':relative,'version':version,'pages':len(pages),'notes':names,
                          'short_pages':[i for i,t in enumerate(pages,1) if len(t.strip())<100]})
        except Exception as error:
            errors.append({'source':relative,'error':str(error)})
            # Retain the last known good version of this document on parse error.
            # The manifest makes this explicit; no false successful import count.
            for old in previous['files']:
                if old.get('source')==relative:
                    active.update(old.get('notes',[]));files.append({**old,'stale':True})
    # Only retire outputs owned by this importer and recorded in its manifest.
    # Move them outside the vault; originals and historical citation snapshots remain.
    retired=cfg.state_path/'paper-imports'/'retired'/dataset
    for old in previous['files']:
        for name in old.get('notes',[]):
            if Path(name).name!=name or name in active:
                continue
            note=folder/name
            if note.is_file() and not note.is_symlink():
                retired.mkdir(parents=True,exist_ok=True)
                note.replace(retired/name)
    report={'dataset':dataset,'files':files,'errors':errors,'unique_documents':len(seen),
            'pages':page_count,'original_bytes':byte_count,'markdown_bytes':sum((folder/n).stat().st_size for n in active),
            'vault_folder':str(folder),'originals_folder':str(originals)}
    manifest_path.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(manifest_path,json.dumps(report,ensure_ascii=False,indent=2))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root',type=Path)
    parser.add_argument('--originals',type=Path,required=True)
    parser.add_argument('--cache',type=Path)
    args=parser.parse_args(argv)
    result=import_papers(args.root,Config.load(),args.originals,args.cache)
    print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False,indent=2))
    if result['errors']:
        raise SystemExit(1)


if __name__=='__main__':main()
