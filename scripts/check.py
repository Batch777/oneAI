"""Run supported main-path checks; legacy tests are explicitly opt-in."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, '-m', 'pytest', 'tests/core', '-q'], cwd=root, check=True)
subprocess.run(['node', '--test', *map(str, sorted((root / 'tests/extension').glob('*.test.mjs')))],
               cwd=root, check=True, env={**os.environ, 'ONEAI_TEST_PYTHON': sys.executable})
subprocess.run(['node', '--test', *map(str, sorted((root / 'tests/client').glob('*.test.cjs')))],
               cwd=root, check=True)
