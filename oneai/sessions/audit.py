"""Deterministic review evidence. Does not execute model-authored commands."""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

def git(cwd,*args):
    return subprocess.check_output(['git','-c','core.fsmonitor=false','-C',str(cwd),*args],timeout=20)

def baseline(cwd):
    return git(cwd,'rev-parse','HEAD').decode().strip()

def build(cwd,base,purpose):
    if not re.fullmatch(r'[a-f0-9]{40,64}',base or ''):raise ValueError('invalid_review_base')
    if not isinstance(purpose,str) or not 1<=len(purpose.strip())<=1000:raise ValueError('review_purpose_required')
    head=baseline(cwd);branch=git(cwd,'branch','--show-current').decode().strip()
    names=set(x.decode('utf-8','replace') for x in git(cwd,'diff','--name-only','-z',base).split(b'\0') if x)
    untracked=[x.decode('utf-8','replace') for x in git(cwd,'ls-files','--others','--exclude-standard','-z').split(b'\0') if x]
    names.update(untracked)
    if len(names)>200:raise ValueError('review_too_many_files')
    digest=hashlib.sha256(git(cwd,'diff','--binary',base));files=[]
    for name in sorted(names):
        path=Path(cwd)/name
        # Untracked files are fingerprinted, never uploaded. No symlink targets.
        if name in untracked:
            digest.update(name.encode())
            if path.is_symlink():digest.update(b'SYMLINK')
            elif path.is_file():
                with path.open('rb') as stream:
                    while chunk:=stream.read(65536):digest.update(chunk)
        files.append({'path':name,'status':'untracked' if name in untracked else 'changed','purpose':'待开发者根据实际 diff 补充作用'})
    dirty=bool(git(cwd,'status','--porcelain'))
    return {'purpose':purpose.strip(),'base_sha':base,'head_sha':head,'branch':branch,'dirty':dirty,'workspace_digest':digest.hexdigest(),'files':files,'tests':'本工具不运行测试；请核对开发者提供的命令与证据。','decision':'审查上述变更；此清单不授权合并或生产部署。','observed_at':time.time()}
