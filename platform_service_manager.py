"""Supervise the original batch worker while the original Codespace is awake.

No request replay: popped UNKNOWN requests and original attempt fences stay put.
Three unexpected process restarts maximum. Explicit SIGTERM maintenance does
not spend or reset that budget. Does not prevent Codespace suspension.
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
    parser.add_argument('--agent-management-grant-id')
    parser.add_argument('--agent-runtime-grant-id')
    parser.add_argument('--gateway-pid', type=int)
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
    if args.agent_management_grant_id is not None:
        if args.gateway_pid is None:
            raise ValueError('ORIGINAL_MANAGEMENT_GATEWAY_PID_REQUIRED')
        host.check('read')
        cmd.extend(['--agent-management-grant-id', args.agent_management_grant_id,
                    '--gateway-pid', str(args.gateway_pid)])
    if args.agent_runtime_grant_id is not None:
        if args.gateway_pid is None:
            raise ValueError('ORIGINAL_RUNTIME_GATEWAY_PID_REQUIRED')
        cmd.extend(['--agent-runtime-grant-id', args.agent_runtime_grant_id])
        if '--gateway-pid' not in cmd:
            cmd.extend(['--gateway-pid', str(args.gateway_pid)])
    exclusive_write(host.root / ('supervisor-' + str(os.getpid()) + '.json'), canonical(dict(
        pid=os.getpid(), generation=host.config['generation'], argv=cmd, started_at=time.time())))
    try:
        failures = 0
        while True:
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
            if proc.returncode in (0, 128 + signal.SIGINT):
                return
            host.check('read')
            if proc.returncode == 128 + signal.SIGTERM:
                # Only the real worker's OS signal handler emits this AFTER
                # cancellation cleanup. Retain the original failure budget.
                time.sleep(1)
                continue
            failures += 1
            if failures > 3:
                raise RuntimeError('WORKER_RESTART_LIMIT_QUERY_ONLY')
            time.sleep(2 ** (failures - 1))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
