"""Resume the same stopped Codespace host without creating enrollment or consent."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler

B = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
R = Path('/home/codespace/.deer-platform-service-20261112')
S = Path('/home/codespace/deer-platform-service-20261112')


def main():
    sys.path[:0] = [str(B), str(S), str(B/'packages/harness'), str(B/'packages/extension-api')]
    from fresh_batch_host import FreshBatchHost
    from app.gateway.platform_account_link import PinnedMcpAccount
    host = FreshBatchHost(R)
    host.check('read')
    original = {n:(R/n).read_bytes() for n in ('host.json','config.sha256')}
    pin = original['config.sha256'].decode().strip()
    account = PinnedMcpAccount(R/'host.json',pin)
    if account.current() is not True or account.config != host.config:
        raise RuntimeError('ORIGINAL_ACCOUNT_CHANGED_STOP')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid():
            continue
        try:
            argv=(p/'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if any(a in (b'app.gateway.app:app',str(S/'fresh_batch_worker.py').encode(),
                     str(S/'platform_service_manager.py').encode()) for a in argv):
            raise RuntimeError('ORIGINAL_PROCESS_ALREADY_PRESENT_STOP')
    probe=socket.socket()
    try:
        if probe.connect_ex(('127.0.0.1',8001))==0:
            raise RuntimeError('ORIGINAL_PORT_OCCUPIED_STOP')
    finally:
        probe.close()
    db=B/'.deer-flow/data/deerflow.db'
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as connection:
        counts=dict(connection.execute('SELECT status,count(*) FROM subagent_batch_items GROUP BY status'))
    if any(v and k not in {'succeeded','failed','cancelled'} for k,v in counts.items()):
        raise RuntimeError('ORIGINAL_TASKS_NOT_IDLE_STOP')
    before=hashlib.sha256((B/'.deer-flow/.jwt_secret').read_bytes()).digest()
    pending=json.loads((R/'dr-management-pending-host-v1/receipt.json').read_text())
    evidence=R/'dr-management-resume-v1'
    evidence.mkdir(mode=0o700)
    (evidence/'executed-source.py').write_bytes(Path(__file__).read_bytes())
    env=dict(os.environ)
    env.update(DEER_FLOW_HOME=str(B/'.deer-flow'),
               DEER_FLOW_SOURCE_ACCOUNT_CONFIG=str(R/'host.json'),
               DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256=pin,
               GATEWAY_CORS_ORIGINS='https://fantastic-space-system-vprp5995g5q43xgv4-8001.app.github.dev',
               XJ_AGENT_MANAGEMENT_CONSENT_ENABLED='true')
    gateway=None
    manager=None
    started=time.time()
    def launch(argv,name):
        with (evidence/name).open('xb') as output:
            return subprocess.Popen(argv,cwd=B,env=env,stdin=subprocess.DEVNULL,
                stdout=output,stderr=output,start_new_session=True)
    try:
        gateway=launch([str(B/'.venv/bin/python'),'-B','-m','uvicorn',
            'app.gateway.app:app','--host','127.0.0.1','--port','8001'],'gateway.log')
        opener=build_opener(ProxyHandler({}))
        def http(path):
            try:
                with opener.open('http://127.0.0.1:8001'+path,timeout=2) as response:
                    return response.status
            except HTTPError as error:
                return error.code
        deadline=time.monotonic()+30
        while True:
            if gateway.poll() is not None:
                raise RuntimeError('ORIGINAL_GATEWAY_EXITED_STOP')
            try:
                if http('/health')==200:
                    break
            except OSError:
                pass
            if time.monotonic()>deadline:
                raise RuntimeError('ORIGINAL_GATEWAY_NOT_HEALTHY_STOP')
            time.sleep(.25)
        if http('/api/v1/auth/me')!=401 or http('/api/v1/auth/agent-management-consent')!=200:
            raise RuntimeError('ORIGINAL_AUTH_BOUNDARY_CHANGED_STOP')
        # Fail if another supervisor owns the original lock; never remove it.
        lock=os.open(R/'supervisor.lock',os.O_RDWR|os.O_NOFOLLOW)
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:
            os.close(lock)
        args=[str(B/'.venv/bin/python'),str(S/'platform_service_manager.py'),
            '--root',str(R),'--gateway-backend',str(B),'--database',str(db),
            '--endpoint','https://ai-gateway-security-review-public-hy1ysqmpn-yoyo-5560.vercel.app/internal/deer-batch/rpc',
            '--agent-management-grant-id',pending['pending_grant_id'],
            '--gateway-pid',str(gateway.pid)]
        manager=launch(args,'manager.log')
        time.sleep(2)
        if manager.poll() is not None:
            raise RuntimeError('ORIGINAL_MANAGER_EXITED_STOP')
        if any((R/n).read_bytes()!=raw for n,raw in original.items()):
            raise RuntimeError('ORIGINAL_HOST_CONFIG_CHANGED_STOP')
        if hashlib.sha256((B/'.deer-flow/.jwt_secret').read_bytes()).digest()!=before:
            raise RuntimeError('ORIGINAL_JWT_CHANGED_STOP')
        receipt={'status':'ORIGINAL_HOST_RESUMED_VERIFY_FRESH_HEARTBEAT',
            'started_at':started,'gateway_pid':gateway.pid,'manager_pid':manager.pid,
            'host_pin_unchanged':True,'jwt_unchanged':True,'idle_counts':counts,
            'management_consent_issued':False,'model_executed':False}
        (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
    except BaseException:
        for child in (manager,gateway):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
        raise


if __name__=='__main__':
    main()
