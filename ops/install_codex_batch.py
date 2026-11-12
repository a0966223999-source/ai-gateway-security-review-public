"""Install only the bounded coordinator into the idle ORIGINAL worker.

No Gateway/auth/config/database changes. The existing manager handles the
worker's maintenance SIGTERM and retains its original arguments and budget.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

SERVICE = Path('/home/codespace/deer-platform-service-20261112')
BACKEND = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
ROOT = Path('/home/codespace/.deer-platform-service-20261112')
BASE = '411272e8ab6e86f53b919c8dcc9f81db54ed5958'
NAMES = ('fresh_batch_host.py', 'fresh_batch_worker.py', 'platform_service_manager.py',
         'platform_codex_executor.py', 'platform_codex_stop.py')


def idle():
    database = BACKEND / '.deer-flow/data/deerflow.db'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        counts = dict(db.execute('SELECT status,count(*) FROM subagent_batch_items GROUP BY status'))
    if any(count and status not in {'succeeded', 'failed', 'cancelled'} for status, count in counts.items()):
        raise RuntimeError('ORIGINAL_TASKS_NOT_IDLE_STOP')
    return counts


def processes(program):
    records = []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = [part.decode() for part in (path / 'cmdline').read_bytes().split(b'\0') if part]
            if program not in args:
                continue
            if Path(os.readlink(path / 'cwd')).resolve() != BACKEND:
                raise RuntimeError('ORIGINAL_PROCESS_CWD_CHANGED_STOP')
            stat = (path / 'stat').read_text().split()
            records.append(dict(pid=int(path.name), parent=int(stat[3]), start=stat[21], argv=args))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return records


def same(record):
    path = Path('/proc') / str(record['pid'])
    try:
        args = [part.decode() for part in (path / 'cmdline').read_bytes().split(b'\0') if part]
        return ((path / 'stat').read_text().split()[21] == record['start']
                and args == record['argv']
                and Path(os.readlink(path / 'cwd')).resolve() == BACKEND)
    except OSError:
        return False


def main():
    stage = Path(__file__).resolve().parents[1]
    originals = {name: (SERVICE / name).read_bytes() for name in NAMES}
    for name, raw in originals.items():
        expected = subprocess.check_output(['git', '-C', str(SERVICE), 'show', BASE + ':' + name])
        if raw != expected:
            raise RuntimeError('CURRENT_ORIGINAL_SOURCE_CHANGED_STOP:' + name)
    new_module = SERVICE / 'platform_codex_batch.py'
    if new_module.exists() or new_module.is_symlink():
        raise RuntimeError('COORDINATOR_EXISTS_QUERY_ONLY')
    sys_path = __import__('sys').path
    sys_path.insert(0, str(SERVICE))
    from fresh_batch_host import FreshBatchHost
    host = FreshBatchHost(ROOT)
    host.check('execute')
    config_before = {name: (ROOT / name).read_bytes() for name in ('host.json', 'config.sha256')}
    if hashlib.sha256(config_before['host.json']).hexdigest() != '66dd5d073a9a92f957307fe69d7c3cc642edef13b10893943fd914ba0c2d127f':
        raise RuntimeError('ORIGINAL_CONFIG_PIN_CHANGED_STOP')
    workers = processes(str(SERVICE / 'fresh_batch_worker.py'))
    managers = processes(str(SERVICE / 'platform_service_manager.py'))
    if len(workers) != 1 or len(managers) != 1 or workers[0]['parent'] != managers[0]['pid']:
        raise RuntimeError('ORIGINAL_MANAGER_WORKER_NOT_BOUND')
    worker, manager = workers[0], managers[0]
    if '--root' not in worker['argv'] or worker['argv'][worker['argv'].index('--root') + 1] != str(ROOT):
        raise RuntimeError('ORIGINAL_WORKER_ROOT_CHANGED_STOP')
    changes = {name: (stage / name).read_bytes() for name in ('fresh_batch_host.py', 'platform_codex_batch.py')}
    for raw in changes.values():
        ast.parse(raw)
    counts = idle()
    time.sleep(1)
    idle()
    if not same(worker) or not same(manager):
        raise RuntimeError('ORIGINAL_PROCESS_CHANGED_STOP')
    evidence = ROOT / 'codex-bounded-batch-install-v1'
    evidence.mkdir(mode=0o700)
    (evidence / 'fresh_batch_host.before').write_bytes(originals['fresh_batch_host.py'])
    (evidence / 'executed-source.py').write_bytes(Path(__file__).read_bytes())
    # Install only two files. Manager, worker, executor, stop, DR, Gateway and
    # configuration bytes remain untouched. Refuse concurrent source changes.
    idle()
    if any((SERVICE / name).read_bytes() != raw for name, raw in originals.items()):
        raise RuntimeError('CONCURRENT_SOURCE_CHANGED_STOP')
    modified = False
    try:
        modified = True
        for name, raw in changes.items():
            target = SERVICE / name
            temporary = target.with_name(target.name + '.bounded-install')
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        if not same(worker) or not same(manager):
            raise RuntimeError('ORIGINAL_PROCESS_CHANGED_BEFORE_RELOAD')
        idle()
        os.kill(worker['pid'], signal.SIGTERM)
        deadline = time.monotonic() + 25
        replacement = None
        while time.monotonic() < deadline:
            if not same(manager):
                raise RuntimeError('ORIGINAL_MANAGER_CHANGED_STOP')
            current = processes(str(SERVICE / 'fresh_batch_worker.py'))
            if (len(current) == 1 and current[0]['pid'] != worker['pid']
                    and current[0]['parent'] == manager['pid'] and current[0]['argv'] == worker['argv']):
                replacement = current[0]
                break
            time.sleep(.25)
        if replacement is None:
            raise RuntimeError('ORIGINAL_WORKER_RELOAD_NOT_OBSERVED')
        time.sleep(2)
        if not same(replacement):
            raise RuntimeError('ORIGINAL_WORKER_RELOAD_EXITED')
        if any((ROOT / name).read_bytes() != raw for name, raw in config_before.items()):
            raise RuntimeError('ORIGINAL_CONFIG_CHANGED_STOP')
        receipt = dict(status='BOUNDED_CODEX_COORDINATOR_INSTALLED', old_worker_pid=worker['pid'],
            worker_pid=replacement['pid'], manager_pid=manager['pid'],
            original_arguments_preserved=True, original_config_pin_unchanged=True,
            source_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in changes.items()},
            idle_sql_counts=counts, model_executed=False, automatic_dispatch=False,
            native_work_dispatch=False, observed_at=time.time())
        (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))
    except BaseException:
        if modified:
            (SERVICE / 'fresh_batch_host.py').write_bytes(originals['fresh_batch_host.py'])
            if new_module.exists() and new_module.read_bytes() == changes['platform_codex_batch.py']:
                new_module.unlink()
            # Ask only the same original manager's replacement worker to reload
            # restored code. Never touch an unrelated PID or spawn a new service.
            if same(manager):
                for current in processes(str(SERVICE / 'fresh_batch_worker.py')):
                    if (current['pid'] != worker['pid'] and current['parent'] == manager['pid']
                            and current['argv'] == worker['argv'] and same(current)):
                        os.kill(current['pid'], signal.SIGTERM)
        raise


if __name__ == '__main__':
    main()

