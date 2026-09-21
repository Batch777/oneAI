"""Run on the relay to register a host; transfer its private config explicitly."""
import argparse
import json
import os
from pathlib import Path
from ..config import Config
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description='Register a oneAI execution host')
    parser.add_argument('--name', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--workspace', action='append', required=True, help='label=/absolute/path/on/execution/host')
    parser.add_argument('--codex', help='absolute path to a modern codex binary on execution host')
    parser.add_argument('--pi', help='absolute path to pi binary on execution host')
    parser.add_argument('--observe-thread', help='bind this exact existing Codex thread read-only; disables runtime creation')
    parser.add_argument('--output', type=Path, required=True, help='new private host config file')
    args = parser.parse_args(argv)
    workspaces = {}
    for item in args.workspace:
        label, sep, path = item.partition('=')
        if not sep or not label or not Path(path).is_absolute() or label in workspaces:
            parser.error('each workspace must be a unique label=/absolute/path')
        workspaces[label] = path
    binaries = {k: v for k, v in {'codex': args.codex, 'pi': args.pi}.items() if v}
    if not binaries or any(not Path(v).is_absolute() for v in binaries.values()):
        parser.error('at least one absolute runtime binary path is required')
    # Exclusive creation prevents accidentally overwriting an existing credential.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        cfg = Config.load()
        store = Store(cfg.state_path/'sessions.sqlite')
        record = store.enroll(args.name, list(workspaces), [] if args.observe_thread else list(binaries), read_only=bool(args.observe_thread))
        config = {**record, 'workspaces': workspaces, 'binaries': binaries, 'server': args.server}
        if args.observe_thread:
            if not args.codex or len(workspaces) != 1:
                raise ValueError('Read-only binding needs Codex and exactly one matching workspace')
            config['bindings'] = [store.bind(record['id'], next(iter(workspaces)), args.name, args.observe_thread)]
        with os.fdopen(fd, 'w') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
        print('Host registered. Private configuration written to:', args.output)
    except Exception:
        args.output.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    main()
