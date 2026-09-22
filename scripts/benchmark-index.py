"""Synthetic incremental index benchmark; does not touch the personal vault."""
from pathlib import Path
from tempfile import TemporaryDirectory
from oneai.indexer import Index
import json
with TemporaryDirectory() as tmp:
    root=Path(tmp);vault=root/'vault';vault.mkdir()
    for n in range(1000):(vault/f'{n}.md').write_text('# Paper\n\n'+('Synthetic research evidence. '*150))
    generated=vault/'inbox/tasks';generated.mkdir(parents=True)
    for n in range(7000):(generated/f'{n}.md').write_text('Generated task, excluded')
    index=Index(root/'index.sqlite')
    index.sync(vault);print('cold',json.dumps(index.last_sync_stats))
    index.sync(vault);print('unchanged',json.dumps(index.last_sync_stats))
    (vault/'1.md').write_text('# Changed paper\n\nNew evidence')
    index.sync(vault);print('one_edit',json.dumps(index.last_sync_stats))
    index.close()
