"""Guarded original-service update. No grant issuance and no model execution.

Run only from the verified complete candidate checkout. Active/attempted tasks
or changed source/process identities stop before formal writes. Explicitly
identified untouched pending rows may remain, with the entire SQL snapshot pinned.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import time

from install_management_consent_page import BACKEND, SERVICE, ROOT, process, alive, stop, status, wait_health


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def maintenance_snapshot(database, *, preserved_items):
    with sqlite3.connect(Path(database).as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute('BEGIN')
        batches=[dict(row) for row in db.execute('SELECT * FROM subagent_batches ORDER BY id')]
        items=[dict(row) for row in db.execute('SELECT * FROM subagent_batch_items ORDER BY id')]
    batch_map={row['id']:row for row in batches}
    counts={}
    for item in items:
        state=item['status'];counts[state]=counts.get(state,0)+1
        if state in {'succeeded','failed','cancelled'}:
            continue
        if (state!='pending' or item['id'] not in preserved_items
                or item['batch_id']!=preserved_items[item['id']] or item['attempt']!=0
                or any(item[k] is not None for k in ('lease_owner','lease_expires_at','started_at','cancel_requested_at'))):
            raise RuntimeError('ORIGINAL_ACTIVE_OR_UNRECOGNIZED_TASK_STOP')
        spec=json.loads(batch_map[item['batch_id']]['execution_spec'])
        if item['id'] in spec.get('native_control',{}).get('items',{}):
            raise RuntimeError('ORIGINAL_PENDING_HAS_NATIVE_FENCE_STOP')
    raw=json.dumps([batches,items],sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return {'counts':counts,'sha256':sha(raw)}


def gateway_hooks(app, middleware):
    anchor = '    install_gateway_management_consent(app, enabled=management_environment.get("XJ_AGENT_MANAGEMENT_CONSENT_ENABLED") == "true")'
    public = '        "/api/v1/auth/agent-management-consent",  # Static review page only.'
    if app.count(anchor) != 1 or middleware.count(public) != 1 or 'install_gateway_runtime_consent' in app:
        raise RuntimeError('ORIGINAL_RUNTIME_HOOK_CHANGED_STOP')
    app = app.replace(anchor, anchor + '\n    from gateway_runtime_consent import install_gateway_runtime_consent\n    install_gateway_runtime_consent(app, enabled=management_environment.get("XJ_FIXED_AGENT_RUNTIME_CONSENT_ENABLED") == "true")')
    middleware = middleware.replace(public, public + '\n        "/api/v1/auth/agent-runtime-consent",  # Static review page only; APIs retain auth/CSRF.')
    ast.parse(app); ast.parse(middleware)
    return app.encode(), middleware.encode()


def start(record, log, *, gateway=False, gateway_pid=None):
    env, argv = dict(record['env']), list(record['argv'])
    if gateway:
        env['XJ_FIXED_AGENT_RUNTIME_CONSENT_ENABLED'] = 'true'
    if gateway_pid is not None:
        index = argv.index('--gateway-pid') + 1
        argv[index] = str(gateway_pid)
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'ab') as output:
        return subprocess.Popen(argv, cwd=BACKEND, env=env, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=output, start_new_session=True)


def main():
    stage = Path(__file__).resolve().parents[1]
    manifest = json.loads((stage / 'ops/fixed-runtime-install-manifest.json').read_text())
    database=BACKEND/'.deer-flow/data/deerflow.db'
    preserved_items=manifest.get('preserved_pending_items',{})
    snapshot=maintenance_snapshot(database,preserved_items=preserved_items)
    counts=snapshot['counts']

    def check_tasks():
        if maintenance_snapshot(database,preserved_items=preserved_items)!=snapshot:
            raise RuntimeError('ORIGINAL_TASK_SNAPSHOT_CHANGED_STOP')
    gateway = process(manifest['gateway_pid'], 'app.gateway.app:app')
    manager = process(manifest['manager_pid'], str(SERVICE / 'platform_service_manager.py'))
    if gateway['start'] != manifest['gateway_start'] or manager['start'] != manifest['manager_start']:
        raise RuntimeError('OBSERVED_ORIGINAL_PROCESSES_CHANGED_STOP')
    if '--agent-runtime-grant-id' in manager['argv']:
        raise RuntimeError('RUNTIME_ALREADY_SELECTED_QUERY_ONLY')
    changes, originals = {}, {}
    roots = {'service': SERVICE, 'backend': BACKEND, 'gateway': BACKEND / 'app/gateway'}
    for entry in manifest['files']:
        target = roots[entry['root']] / entry['target']
        before = target.read_bytes() if target.exists() else None
        if (None if before is None else sha(before)) != entry['before_sha256']:
            raise RuntimeError('ORIGINAL_RUNTIME_SOURCE_CHANGED_STOP:' + entry['target'])
        raw = (stage / entry['source']).read_bytes()
        if sha(raw) != entry['after_sha256']:
            raise RuntimeError('CANDIDATE_RUNTIME_SOURCE_CHANGED_STOP')
        if target.suffix == '.py': ast.parse(raw)
        changes[target], originals[target] = raw, before
    app_path, auth_path = BACKEND/'app/gateway/app.py', BACKEND/'app/gateway/auth_middleware.py'
    originals[app_path], originals[auth_path] = app_path.read_bytes(), auth_path.read_bytes()
    if sha(originals[app_path]) != manifest['app_sha256'] or sha(originals[auth_path]) != manifest['auth_sha256']:
        raise RuntimeError('ORIGINAL_GATEWAY_SOURCE_CHANGED_STOP')
    changes[app_path], changes[auth_path] = gateway_hooks(originals[app_path].decode(),originals[auth_path].decode())
    pins = {p:p.read_bytes() for p in (ROOT/'host.json',ROOT/'config.sha256',BACKEND/'.deer-flow/.jwt_secret')}
    time.sleep(1)
    check_tasks()
    if not alive(gateway) or not alive(manager):
        raise RuntimeError('ORIGINAL_PROCESS_CHANGED_STOP')
    evidence = ROOT/'dr-fixed-runtime-install-v1'
    evidence.mkdir(mode=0o700)
    for index, (path, before) in enumerate(originals.items()):
        if before is not None:
            backup=evidence/f'{index}.before'
            backup.write_bytes(before);backup.chmod(0o600)
    modified = False
    new_gateway = new_manager = None
    try:
        stop(manager)  # Original supervisor drains its own worker first.
        check_tasks()
        stop(gateway)
        check_tasks()
        if any((p.read_bytes() if p.exists() else None) != raw for p,raw in originals.items()):
            raise RuntimeError('CONCURRENT_ORIGINAL_SOURCE_CHANGED_STOP')
        modified = True
        for path,raw in changes.items():
            temporary=path.with_name(path.name+'.fixed-runtime-install')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
        new_gateway = start(gateway,evidence/'gateway.log',gateway=True)
        wait_health()
        if status('/api/v1/auth/me')[0] != 401 or status('/api/v1/auth/agent-runtime-consent')[0] != 200:
            raise RuntimeError('ORIGINAL_RUNTIME_LOGIN_BOUNDARY_NOT_READY')
        if any(p.read_bytes() != raw for p,raw in pins.items()):
            raise RuntimeError('ORIGINAL_AUTH_OR_CONFIG_PIN_CHANGED_STOP')
        check_tasks()
        new_manager = start(manager,evidence/'manager.log',gateway_pid=new_gateway.pid)
        time.sleep(2)
        if new_gateway.poll() is not None or new_manager.poll() is not None:
            raise RuntimeError('ORIGINAL_RUNTIME_RELOAD_EXITED')
        receipt=dict(status='SOURCE_INSTALLED_RUNTIME_GRANT_NOT_SELECTED',gateway_pid=new_gateway.pid,
            manager_pid=new_manager.pid,original_sql_counts=counts,pins_unchanged=True,
            original_sql_snapshot_sha256=snapshot['sha256'],pending_and_fences_preserved=True,
            runtime_grant_issued=False,model_executed=False,observed_at=time.time())
        (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
    except BaseException:
        for child in (new_manager,new_gateway):
            if child is not None and child.poll() is None:
                child.terminate();child.wait(timeout=15)
        if modified:
            for path,raw in originals.items():
                if raw is None:
                    if path.exists() and path.read_bytes() == changes[path]:path.unlink()
                elif path.exists() and path.read_bytes() == changes[path]:path.write_bytes(raw)
                elif path.exists() and path.read_bytes() != raw:
                    raise RuntimeError('CONCURRENT_SOURCE_PREVENTS_ROLLBACK_STOP')
        restored = gateway if alive(gateway) else start(gateway,evidence/'gateway-restore.log')
        wait_health()
        if not alive(manager):start(manager,evidence/'manager-restore.log',gateway_pid=restored['pid'] if isinstance(restored,dict) else restored.pid)
        raise


if __name__ == '__main__':
    main()
