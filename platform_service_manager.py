"""Supervise the original batch worker while the original Codespace is awake.

No request replay: popped UNKNOWN requests and original attempt fences stay put.
Three process restarts maximum. Does not prevent Codespace suspension.
"""
import argparse
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from fresh_batch_host import FreshBatchHost, canonical, exclusive_write


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'gateway-backend', 'database', 'endpoint'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    host = FreshBatchHost(args.root)
    if host.config.get('lifecycle') != 'owner-platform-service-v1':
        raise ValueError('PLATFORM_SERVICE_ENROLLMENT_REQUIRED')
    fd = os.open(host.root / 'supervisor.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    cmd = [sys.executable, str(Path(__file__).with_name('fresh_batch_worker.py')),
           '--root', args.root, '--gateway-backend', args.gateway_backend,
           '--database', args.database, '--endpoint', args.endpoint]
    exclusive_write(host.root / ('supervisor-' + str(os.getpid()) + '.json'), canonical(dict(
        pid=os.getpid(), generation=host.config['generation'], argv=cmd, started_at=time.time())))
    try:
        for attempt in range(4):
            host.check('read')
            if stopping:
                return
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL)
            try:
                while proc.poll() is None:
                    host.check('read')
                    if stopping:
                        return
                    time.sleep(1)
            finally:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=7)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            if proc.returncode == 0:
                return
            host.check('read')
            if attempt < 3:
                time.sleep(2 ** attempt)
        raise RuntimeError('WORKER_RESTART_LIMIT_QUERY_ONLY')
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
