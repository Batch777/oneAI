"""Owner-operated Git release switcher. No network polling or model approval.

Candidates must already be trusted and tested. Staging only: dependency/schema
changes require a new environment and migration review before production use.
"""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import urllib.request


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temp.replace(path)


def files_digest(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob('*')) if p.is_file() and p.name != '.release.json'
            and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def stage(repo, root, sha):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('exact_commit_required')
    data = subprocess.check_output(['git', '-C', str(repo), 'archive', sha])
    target = root / 'releases' / sha
    if target.exists():
        verify(target)
        return target
    target.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive.getmembers():
            if item.issym() or item.islnk() or not (item.isfile() or item.isdir()):
                raise ValueError('unsupported_archive_member')
            if Path(item.name).is_absolute() or '..' in Path(item.name).parts:
                raise ValueError('unsafe_archive_path')
        archive.extractall(target, filter='data')
    write_json(target / '.release.json', {'commit': sha, 'files': files_digest(target)})
    return target


def verify(target):
    manifest = json.loads((target / '.release.json').read_text())
    if manifest['commit'] != target.name or manifest['files'] != files_digest(target):
        raise ValueError('release_integrity_failed')


def switch(root, sha):
    temp = root / 'next'
    temp.unlink(missing_ok=True)
    if sha:
        temp.symlink_to(Path('releases') / sha)
        temp.replace(root / 'current')
    else:
        (root / 'current').unlink(missing_ok=True)
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def activate(root, sha, restart, health):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('exact_commit_required')
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal = root / 'deployment.json'
        if journal.exists():
            prior = json.loads(journal.read_text())
            if prior['phase'] in ('switching', 'rolling_back'):
                raise RuntimeError('incomplete_deployment_requires_recover')
        verify(root / 'releases' / sha)
        previous = (root / 'current').resolve().name if (root / 'current').exists() else None
        state = {'previous': previous, 'candidate': sha, 'phase': 'switching'}
        write_json(journal, state)
        switch(root, sha)
        try:
            restart()
            if not health(sha):
                raise RuntimeError('candidate_health_failed')
        except Exception:
            state['phase'] = 'rolling_back'; write_json(journal, state)
            switch(root, previous)
            restart()
            if previous and not health(previous):
                state['phase'] = 'rollback_failed'; write_json(journal, state)
                raise RuntimeError('rollback_health_failed')
            state['phase'] = 'rolled_back'; write_json(journal, state)
            raise
        state['phase'] = 'healthy'; write_json(journal, state)
        return state


def recover(root, restart, health):
    with (root / 'deploy.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / 'deployment.json'; state = json.loads(path.read_text())
        if state['phase'] not in ('switching', 'rolling_back', 'rollback_failed'):
            raise ValueError('no_recovery_needed')
        previous = state['previous']
        if previous:
            verify(root / 'releases' / previous)
        switch(root, previous); restart()
        if previous and not health(previous):
            raise RuntimeError('recovery_health_failed')
        state['phase'] = 'rolled_back'; write_json(path, state)
        return state


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['stage', 'activate', 'recover'])
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--repo', type=Path)
    p.add_argument('--commit')
    p.add_argument('--service', default='oneai-staging')
    p.add_argument('--health-url', default='http://127.0.0.1:18765/api/version')
    a = p.parse_args()
    if a.action == 'stage':
        print(stage(a.repo, a.root, a.commit)); return
    if not re.fullmatch(r'oneai-[a-z0-9-]+', a.service):
        raise ValueError('invalid_service')
    def restart():
        subprocess.run(['systemctl', '--user', 'restart', a.service], check=True)
    def health(sha):
        for _ in range(30):
            try:
                with urllib.request.urlopen(a.health_url, timeout=2) as response:
                    if json.load(response).get('commit') == sha:
                        return True
            except (OSError, ValueError):
                pass
            time.sleep(.5)
        return False
    print(json.dumps(recover(a.root, restart, health) if a.action == 'recover'
                     else activate(a.root, a.commit, restart, health)))


if __name__ == '__main__':
    main()
