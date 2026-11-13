"""Stage the original management consumer for a FUTURE interactive consent.

Selects a record ID only. It never creates or renews consent, changes the host
configuration/pin, creates an Agent, or authenticates a model-supplied owner.
"""
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

BACKEND = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
SOURCE = Path('/home/codespace/deer-platform-service-20261112')
ROOT = Path('/home/codespace/.deer-platform-service-20261112')
THREAD = '68ef369d-bf4f-4fe6-b11f-f6ac79296a40'
OPERATION = 'dr-consent-20261113-01'
LINK = '7461364a3b6a4ba0b43a45f1774e9dc0d2b02ec46ad58c4f9d6f9803635fbbeb'
GUARDS = {
    'fresh_batch_host.py': 'd300f1a7fa3f948f9a3cf592798f860242a5678e08d7d1aff244a594b47f4bbf',
    'fresh_batch_worker.py': '3ade5b2cf4a05653ef92e07562a54d79affd74ba79ee8e98bc7a9d422b5d5f18',
    'platform_service_manager.py': '328f11c1ddc5ee0fb745695c591f903832361761264c879a5f44c92dfbe7f1ee',
}
MODULES = ('deer_agent_management.py', 'deer_management_consent.py',
           'deer_management_journal.py', 'deer_management_host.py',
           'deer_management_rpc.py', 'worker_signal_lifecycle.py')


def main():
    stage = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(stage / 'ops'), str(BACKEND), str(BACKEND / 'packages/harness'),
                   str(BACKEND / 'packages/extension-api'), str(SOURCE)]
    from install_management_consent_page import process, stop, start, alive, idle
    from fresh_batch_host import FreshBatchHost, canonical
    from app.gateway import platform_task_source as source_io
    host = FreshBatchHost(ROOT)
    host.check('read')
    if not {'read', 'execute'} <= set(host.config['actions']):
        raise RuntimeError('ORIGINAL_TRANSPORT_NOT_ENROLLED')
    config_before = {name: (ROOT / name).read_bytes() for name in ('host.json', 'config.sha256')}
    gateway = process(56604, 'app.gateway.app:app')
    worker = process(56805, str(SOURCE / 'fresh_batch_worker.py'))
    old_sources = {}
    for name, expected in GUARDS.items():
        raw = (SOURCE / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError('ORIGINAL_WORKER_SOURCE_CHANGED_STOP:' + name)
        old_sources[name] = raw
    for name in MODULES:
        if (SOURCE / name).exists():
            raise RuntimeError('MANAGEMENT_MODULE_EXISTS_QUERY_ONLY:' + name)
    fd = source_io.private_directory(BACKEND / '.deer-flow/platform-sources')
    try:
        link = json.loads(source_io.read_at(fd, 'account-link-' + LINK + '.json'))
        try:
            os.stat('account-link-' + LINK + '.revoked', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError('IDENTITY_LOOKUP_REVOKED_STOP')
    finally:
        os.close(fd)
    if (link.get('thread_id') != THREAD or link.get('mcp_owner') != host.config['owner']
            or link.get('generation') != host.config['generation']
            or link.get('scope') != 'platform-checkpoint-only'
            or link.get('native_work_authority') is not False):
        raise RuntimeError('ORIGINAL_IDENTITY_LOOKUP_MISMATCH')
    database = BACKEND / '.deer-flow/data/deerflow.db'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT system_role,needs_setup FROM users WHERE id=?',
                         (link['gateway_user_id'],)).fetchone()
        thread = db.execute('SELECT user_id FROM threads_meta WHERE thread_id=?', (THREAD,)).fetchone()
    if row != ('admin', 0) or thread != (link['gateway_user_id'],):
        raise RuntimeError('PENDING_SELECTOR_LIVE_OWNER_MISMATCH')
    # This pure hash is the FUTURE lookup slot, not a grant or authentication.
    pending = hashlib.sha256(canonical([host.config['owner'], host.config['generation'],
        link['gateway_user_id'], THREAD, OPERATION])).hexdigest()
    argv = worker['argv']
    options = {name: argv[argv.index('--' + name) + 1]
               for name in ('root', 'gateway-backend', 'database', 'endpoint')}
    if (Path(options['root']).resolve() != ROOT or Path(options['database']).resolve() != database
            or Path(options['gateway-backend']).resolve() != BACKEND):
        raise RuntimeError('ORIGINAL_WORKER_PROFILE_CHANGED')
    changes = {name: (stage / name).read_bytes() for name in (*GUARDS, *MODULES)}
    for raw in changes.values():
        ast.parse(raw)
    consent_target = BACKEND / 'deer_management_consent.py'
    consent_before = consent_target.read_bytes()
    expected_consent = subprocess.check_output(['git', '-C', str(SOURCE), 'show',
        '84a0640e80ecae7e9c239783263a3a3cf46ad069:deer_management_consent.py'])
    if consent_before != expected_consent:
        raise RuntimeError('ORIGINAL_ISSUER_MODULE_CHANGED_STOP')
    counts = idle(database)
    time.sleep(1)
    idle(database)
    evidence = ROOT / 'dr-management-pending-host-v1'
    evidence.mkdir(mode=0o700)
    lock = os.open(ROOT / 'supervisor.lock', os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    (evidence / 'executed-source.py').write_bytes(Path(__file__).read_bytes())
    for name, raw in old_sources.items():
        (evidence / (name + '.before')).write_bytes(raw)
    (evidence / 'issuer-consent.before').write_bytes(consent_before)
    modified = False
    manager = None
    try:
        idle(database)
        stop(worker)
        if not alive(gateway):
            raise RuntimeError('ORIGINAL_GATEWAY_CHANGED_STOP')
        for name, raw in old_sources.items():
            if (SOURCE / name).read_bytes() != raw:
                raise RuntimeError('CONCURRENT_SOURCE_CHANGED_STOP')
        modified = True
        for name, raw in changes.items():
            target = SOURCE / name
            temporary = target.with_name(target.name + '.dr-pending-install')
            with temporary.open('xb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        # Only the unissued-consent rejection is updated; the running Gateway
        # retains its existing issuer implementation and authenticated session.
        consent_target.write_bytes(changes['deer_management_consent.py'])
        args = [argv[0], str(SOURCE / 'platform_service_manager.py')]
        for name, value in options.items():
            args.extend(['--' + name, value])
        args.extend(['--agent-management-grant-id', pending, '--gateway-pid', str(gateway['pid'])])
        profile = {**worker, 'argv': args}
        # The original manager acquires this same lock. Hand ownership back
        # before launch; retaining it would make the manager exit immediately.
        os.close(lock)
        lock = None
        manager = start(profile, evidence / 'manager.log')
        time.sleep(1)
        if manager.poll() is not None:
            raise RuntimeError('ORIGINAL_MANAGEMENT_MANAGER_START_FAILED')
        if any((ROOT / name).read_bytes() != raw for name, raw in config_before.items()):
            raise RuntimeError('ORIGINAL_CONFIG_CHANGED_STOP')
        receipt = {'status': 'ORIGINAL_MANAGEMENT_CONSUMER_STAGED_PENDING_CONSENT',
            'manager_pid': manager.pid, 'gateway_pid': gateway['pid'],
            'pending_grant_id': pending, 'operation_key': OPERATION,
            'pending_selector_is_authority': False, 'management_grant_issued_by_this_script': False,
            'host_config_pin_unchanged': True, 'generation_unchanged': True,
            'identity_link_used_as_authority': False, 'idle_sql_counts': counts,
            'started_at': time.time(), 'model_executed': False}
        (evidence / 'receipt.json').write_text(json.dumps(receipt, indent=2))
        print(json.dumps(receipt))
    except BaseException:
        if manager is not None and manager.poll() is None:
            manager.terminate()
            manager.wait(timeout=20)
        if modified:
            for name, raw in old_sources.items():
                (SOURCE / name).write_bytes(raw)
            consent_target.write_bytes(consent_before)
        if not alive(worker):
            start(worker, evidence / 'rollback-worker.log')
        raise
    finally:
        if lock is not None:
            os.close(lock)


if __name__ == '__main__':
    main()
