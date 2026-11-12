"""One controlled original-Gateway reload after original SQL is idle.

No grant is issued here. Original worker arguments/environment and Gateway
session configuration are retained in memory and reused, never exported.
"""
import ast
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

BACKEND = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
SERVICE = Path('/home/codespace/deer-platform-service-20261112')
GATEWAY = BACKEND / 'app/gateway'
ROOT = Path('/home/codespace/.deer-platform-service-20261112')
ASSETS = ('deer_agent_management.py', 'deer_management_consent.py',
          'gateway_agent_consent.py', 'agent_management_consent.html')
APP_SHA = '55dacb6c1af0d8881bba983be3385fef3327d83775f06bc1945ff44eab279faa'
WORKER_SHA = '3ade5b2cf4a05653ef92e07562a54d79affd74ba79ee8e98bc7a9d422b5d5f18'


def idle(database):
    with sqlite3.connect(Path(database).as_uri() + '?mode=ro', uri=True) as db:
        states = dict(db.execute('SELECT status,count(*) FROM subagent_batch_items GROUP BY status'))
    if any(count and state not in {'succeeded', 'failed', 'cancelled'} for state, count in states.items()):
        raise RuntimeError('ORIGINAL_TASKS_NOT_IDLE_STOP')
    return states


def process(pid, program):
    root = Path('/proc') / str(pid)
    args = [a.decode() for a in (root / 'cmdline').read_bytes().split(b'\0') if a]
    if program not in args or Path(os.readlink(root / 'cwd')).resolve() != BACKEND:
        raise RuntimeError('ORIGINAL_PROCESS_CHANGED_STOP')
    return {'pid': pid, 'start': (root / 'stat').read_text().split()[21], 'argv': args,
            'env': dict(item.split('=', 1) for item in (root / 'environ').read_bytes().decode().split('\0') if '=' in item)}


def alive(record):
    try:
        root = Path('/proc') / str(record['pid'])
        return ((root / 'stat').read_text().split()[21] == record['start']
                and bool((root / 'cmdline').read_bytes().strip(b'\0')))
    except OSError:
        return False


def stop(record):
    if not alive(record):
        raise RuntimeError('ORIGINAL_PROCESS_IDENTITY_CHANGED_STOP')
    os.kill(record['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 15
    while alive(record) and time.monotonic() < deadline:
        time.sleep(.1)
    if alive(record):
        raise RuntimeError('ORIGINAL_PROCESS_DID_NOT_DRAIN_STOP')


def start(record, log, *, issuer=False):
    env = dict(record['env'])
    if issuer:
        env['XJ_AGENT_MANAGEMENT_CONSENT_ENABLED'] = 'true'
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'ab') as output:
        return subprocess.Popen(record['argv'], cwd=BACKEND, env=env,
            stdin=subprocess.DEVNULL, stdout=output, stderr=output, start_new_session=True)


def status(path):
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:8001' + path, timeout=2) as response:
            return response.status, response.read(2000000)
    except HTTPError as error:
        return error.code, b''


def wait_health():
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            if status('/health')[0] == 200:
                return
        except OSError:
            pass
        time.sleep(.25)
    raise RuntimeError('ORIGINAL_GATEWAY_RELOAD_NOT_HEALTHY')


def main():
    stage = Path(__file__).resolve().parents[1]
    evidence = ROOT / 'dr-consent-page-install-v1'
    database = BACKEND / '.deer-flow/data/deerflow.db'
    originals = {name: (GATEWAY / name).read_bytes() for name in ('app.py', 'auth_middleware.py')}
    if hashlib.sha256(originals['app.py']).hexdigest() != APP_SHA:
        raise RuntimeError('GATEWAY_SOURCE_CHANGED_STOP')
    if hashlib.sha256((SERVICE / 'fresh_batch_worker.py').read_bytes()).hexdigest() != WORKER_SHA:
        raise RuntimeError('WORKER_SOURCE_CHANGED_STOP')
    if (ROOT / 'STOP').exists() or (ROOT / 'STOP').is_symlink():
        raise RuntimeError('ORIGINAL_HOST_STOPPED')
    # These observed original PIDs/start times are rechecked before each signal.
    gateway = process(27703, 'app.gateway.app:app')
    worker = process(29077, str(SERVICE / 'fresh_batch_worker.py'))
    if gateway['start'] != '185243' or worker['start'] != '196575':
        raise RuntimeError('OBSERVED_PROCESS_CHANGED_STOP')
    if not {'127.0.0.1', '8001'} <= set(gateway['argv']):
        raise RuntimeError('ORIGINAL_LOOPBACK_GATEWAY_REQUIRED')
    payload = {name: (stage / name).read_bytes() for name in ASSETS}
    if any((BACKEND / name).exists() for name in payload):
        raise RuntimeError('MANAGEMENT_SOURCE_EXISTS_QUERY_ONLY')
    app = originals['app.py'].decode()
    anchor = '    install_account_link_routes(app)'
    if app.count(anchor) != 1:
        raise RuntimeError('ORIGINAL_GATEWAY_HOOK_CHANGED')
    app = app.replace(anchor, anchor + '\n    from gateway_agent_consent import install_gateway_management_consent\n    from os import environ as management_environment\n    install_gateway_management_consent(app, enabled=management_environment.get("XJ_AGENT_MANAGEMENT_CONSENT_ENABLED") == "true")')
    middleware = originals['auth_middleware.py'].decode()
    anchor = '        "/api/v1/auth/continuation-link",'
    if middleware.count(anchor) != 1:
        raise RuntimeError('ORIGINAL_PUBLIC_PAGE_BOUNDARY_CHANGED')
    middleware = middleware.replace(anchor, '        "/api/v1/auth/agent-management-consent",  # Static review page only.\n' + anchor)
    for name, raw in {**payload, 'app.py': app.encode(), 'auth_middleware.py': middleware.encode()}.items():
        if name.endswith('.py'):
            ast.parse(raw)
    # Two fresh SQL reads distinguish the resident worker from in-flight tasks.
    counts = idle(database)
    time.sleep(1)
    idle(database)
    evidence.mkdir(mode=0o700)
    lock = os.open(ROOT / 'supervisor.lock', os.O_RDWR | os.O_NOFOLLOW)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    jwt = BACKEND / '.deer-flow/.jwt_secret'
    jwt_hash = hashlib.sha256(jwt.read_bytes()).digest()
    for name, raw in originals.items():
        (evidence / (name + '.before')).write_bytes(raw)
    (evidence / 'executed-source.py').write_bytes(Path(__file__).read_bytes())
    modified = False
    new_gateway = new_worker = None
    try:
        # Worker first: no new receiver poll while Gateway is switched.
        idle(database)
        stop(worker)
        idle(database)
        stop(gateway)
        for name, raw in originals.items():
            if (GATEWAY / name).read_bytes() != raw:
                raise RuntimeError('CONCURRENT_GATEWAY_SOURCE_CHANGED_STOP')
        for name, raw in payload.items():
            with (BACKEND / name).open('xb') as output:
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
        modified = True
        (GATEWAY / 'app.py').write_text(app)
        (GATEWAY / 'auth_middleware.py').write_text(middleware)
        new_gateway = start(gateway, evidence / 'gateway.log', issuer=True)
        wait_health()
        if status('/api/v1/auth/me')[0] != 401:
            raise RuntimeError('ORIGINAL_LOGIN_PROTECTION_CHANGED')
        code, page = status('/api/v1/auth/agent-management-consent')
        if code != 200 or b'qa-dr-mcp-20261112' not in page:
            raise RuntimeError('MANAGEMENT_REVIEW_PAGE_NOT_READY')
        _, raw = status('/openapi.json')
        if '/api/threads/{thread_id}/agent-management-consents' not in json.loads(raw)['paths']:
            raise RuntimeError('MANAGEMENT_ISSUER_ROUTE_NOT_INSTALLED')
        new_worker = start(worker, evidence / 'worker.log')
        if hashlib.sha256(jwt.read_bytes()).digest() != jwt_hash:
            raise RuntimeError('ORIGINAL_SESSION_SECRET_CHANGED')
        report = {'status': 'ORIGINAL_MANAGEMENT_CONSENT_PAGE_INSTALLED',
            'gateway_pid': new_gateway.pid, 'worker_pid': new_worker.pid,
            'health_http': 200, 'anonymous_me_http': 401, 'review_page_http': 200,
            'idle_sql_counts': counts, 'jwt_unchanged': True,
            'original_worker_code_unchanged': True, 'management_grant_issued': False,
            'model_executed': False, 'pin_keys_and_config_unchanged': True}
        (evidence / 'receipt.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    except BaseException:
        if new_worker is not None and new_worker.poll() is None:
            new_worker.terminate()
            new_worker.wait(timeout=15)
        if new_gateway is not None and new_gateway.poll() is None:
            new_gateway.terminate()
            new_gateway.wait(timeout=15)
        if modified:
            for name, raw in originals.items():
                (GATEWAY / name).write_bytes(raw)
        if not alive(gateway):
            start(gateway, evidence / 'rollback-gateway.log')
            wait_health()
        if not alive(worker):
            start(worker, evidence / 'rollback-worker.log')
        raise
    finally:
        os.close(lock)


if __name__ == '__main__':
    main()
