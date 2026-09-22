from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from oneai.vault import iter_markdown
from oneai.context import load_context
from oneai.config import Config
with TemporaryDirectory() as tmp:
 root=Path(tmp);cfg=Config(root/'vault',root/'state',None);cfg.ensure_dirs()
 (cfg.vault_path/'rules').mkdir();(cfg.vault_path/'rules/date.md').write_text('Do not confuse deadlines and dates')
 folder=cfg.vault_path/'inbox/tasks';folder.mkdir(parents=True)
 for n in range(7000):(folder/f'{n}.md').write_text('synthetic record')
 before=perf_counter();old=[p for p in iter_markdown(cfg.vault_path) if p.relative_to(cfg.vault_path).parts[0]=='rules'];old_ms=(perf_counter()-before)*1000
 before=perf_counter();new=load_context(cfg);new_ms=(perf_counter()-before)*1000
 print({'synthetic_files':7000,'old_walk_ms':round(old_ms,2),'rules_only_ms':round(new_ms,2)})
