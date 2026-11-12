"""Guarded repair of the original stopped worker; preserve checkpoint wiring."""
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

SOURCE = Path('/home/codespace/deer-platform-service-20261112')
ROOT = Path('/home/codespace/.deer-platform-service-20261112')
GUARDS = {
    'fresh_batch_worker.py': '3ade5b2cf4a05653ef92e07562a54d79affd74ba79ee8e98bc7a9d422b5d5f18',
    'platform_service_manager.py': '328f11c1ddc5ee0fb745695c591f903832361761264c879a5f44c92dfbe7f1ee',
}


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError('RECOVERY_SOURCE_CHANGED_STOP')
    return source.replace(before, after)


def main():
    sys.path.insert(0, str(SOURCE))
    from fresh_batch_host import FreshBatchHost, exclusive_write, canonical
    host = FreshBatchHost(ROOT)
    host.check('read')
    fd = os.open(ROOT / 'supervisor.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for proc in Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                args = (proc / 'cmdline').read_bytes().split(b'\0')
            except OSError:
                continue
            if any(arg in {str(SOURCE / 'fresh_batch_worker.py').encode(),
                           str(SOURCE / 'platform_service_manager.py').encode()} for arg in args):
                raise RuntimeError('ORIGINAL_SERVICE_BUSY_STOP')
        originals = {}
        for name, expected in GUARDS.items():
            raw = (SOURCE / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError('ORIGINAL_SOURCE_HASH_CHANGED_STOP:' + name)
            originals[name] = raw.decode()
        helper = (Path(__file__).resolve().parents[1] / 'worker_signal_lifecycle.py').read_text()
        ast.parse(helper)
        if (SOURCE / 'worker_signal_lifecycle.py').exists():
            raise RuntimeError('RECOVERY_HELPER_EXISTS_QUERY_ONLY')
        worker = replace_once(originals['fresh_batch_worker.py'], '''    async def run():
        task = asyncio.create_task(serve(args))
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
        await task
    asyncio.run(run())''', '''    from worker_signal_lifecycle import run_until_signalled
    raise SystemExit(asyncio.run(run_until_signalled(serve, args)))''')
        manager = replace_once(originals['platform_service_manager.py'],
            '        for attempt in range(4):', '        failures = 0\n        while True:')
        manager = replace_once(manager, '            if proc.returncode == 0:',
                              '            if proc.returncode in (0, 128 + signal.SIGINT):')
        manager = replace_once(manager, '''            if attempt < 3:
                time.sleep(2 ** attempt)
        raise RuntimeError('WORKER_RESTART_LIMIT_QUERY_ONLY')''', '''            if proc.returncode == 128 + signal.SIGTERM:
                time.sleep(1)
                continue
            failures += 1
            if failures > 3:
                raise RuntimeError('WORKER_RESTART_LIMIT_QUERY_ONLY')
            time.sleep(2 ** (failures - 1))''')
        changes = {'fresh_batch_worker.py': worker, 'platform_service_manager.py': manager,
                   'worker_signal_lifecycle.py': helper}
        for source in changes.values():
            ast.parse(source)
        evidence = ROOT / 'recovery-worker-lifecycle-v1'
        evidence.mkdir(mode=0o700)
        exclusive_write(evidence / 'executed-source.py', Path(__file__).read_bytes())
        for name, source in originals.items():
            exclusive_write(evidence / (name + '.before'), source.encode())
        for name, source in changes.items():
            host.check('read')
            target = SOURCE / name
            if name in GUARDS and hashlib.sha256(target.read_bytes()).hexdigest() != GUARDS[name]:
                raise RuntimeError('CONCURRENT_SOURCE_CHANGED_STOP')
            temporary = SOURCE / (name + '.dr-recovery-new')
            exclusive_write(temporary, source.encode())
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644)
            os.replace(temporary, target)
            directory = os.open(SOURCE, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        report = {'status': 'ORIGINAL_WORKER_LIFECYCLE_REPAIRED_NOT_STARTED',
            'checkpoint_forward_preserved': 'platform-checkpoint-v1' in worker,
            'unexpected_restart_limit': 3, 'maintenance_exit_code': 143,
            'old_logs_and_attempts_preserved': True,
            'installed_sha256': {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest()
                                 for name in changes}}
        exclusive_write(evidence / 'receipt.json', canonical(report))
        print(json.dumps(report))
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
