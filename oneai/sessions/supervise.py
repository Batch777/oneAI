"""Ensure an orphaned host cannot leave an invisible agent/tools running (POSIX)."""
import os
import signal
import subprocess
import sys
import time


def main():
    parent = int(sys.argv[1])
    process = subprocess.Popen(sys.argv[2:], start_new_session=True)
    def stop(*_):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while process.poll() is None:
        if os.getppid() != parent:
            stop()
        time.sleep(0.2)
    return process.returncode


if __name__ == '__main__':
    sys.exit(main())
