"""One guarded maintenance resume of the original idle Codespace service.

Preserves enrollment, private JWT and both original consent IDs. Expired/revoked
consents remain expired/revoked; startup grants no replacement authority.
"""
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
from urllib.request import build_opener, ProxyHandler
from urllib.error import HTTPError

B=Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
R=Path('/home/codespace/.deer-platform-service-20261112')
S=Path('/home/codespace/deer-platform-service-20261112')

def main():
    os.chdir(B)
    sys.path[:0]=[str(S),str(B),str(B/'packages/harness'),str(B/'packages/extension-api')]
    from fresh_batch_host import FreshBatchHost
    from app.gateway.platform_account_link import PinnedMcpAccount
    host=FreshBatchHost(R)
    expected_generation='f8a69fac5f1b2acd07918b0afe7348c5'
    if host.config['generation']!=expected_generation:raise RuntimeError('ORIGINAL_GENERATION_CHANGED')
    account=PinnedMcpAccount(R/'host.json',host.pin)
    if account.current() is not True or account.config!=host.config:raise RuntimeError('ORIGINAL_ACCOUNT_CHANGED')
    latest=max((json.loads(p.read_text()) for p in R.glob('supervisor-*.json')),key=lambda x:x['started_at'])
    old=list(latest['argv'])
    if old[:2]!=[str(B/'.venv/bin/python'),str(S/'fresh_batch_worker.py')]:raise RuntimeError('ORIGINAL_ARGV_CHANGED')
    required={'--root':str(R),'--gateway-backend':str(B),'--database':str(B/'.deer-flow/data/deerflow.db')}
    for key,value in required.items():
        if old.count(key)!=1 or old[old.index(key)+1]!=value:raise RuntimeError('ORIGINAL_ARGV_SCOPE_CHANGED')
    for key in ['--agent-management-grant-id','--agent-runtime-grant-id','--gateway-pid','--endpoint']:
        if old.count(key)!=1:raise RuntimeError('ORIGINAL_CAPABILITY_ARGV_MISSING')
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid():continue
        try:args=(p/'cmdline').read_bytes().split(b'\0')
        except OSError:continue
        if any(a in (b'app.gateway.app:app',str(S/'fresh_batch_worker.py').encode(),
                str(S/'platform_service_manager.py').encode()) for a in args):
            raise RuntimeError('ORIGINAL_PROCESS_ALREADY_PRESENT')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',8001))==0:raise RuntimeError('ORIGINAL_PORT_OCCUPIED')
    with sqlite3.connect((B/'.deer-flow/data/deerflow.db').as_uri()+'?mode=ro',uri=True) as db:
        counts=dict(db.execute('select status,count(*) from subagent_batch_items group by status'))
    if any(v for k,v in counts.items() if k not in {'succeeded','failed','cancelled'}):
        raise RuntimeError('ORIGINAL_TASKS_NOT_IDLE')
    pins={p:p.read_bytes() for p in (R/'host.json',R/'config.sha256',B/'.deer-flow/.jwt_secret')}
    evidence=R/'dag-v2-maintenance-resume-v1';evidence.mkdir(mode=0o700)
    # An existing directory rejects repeated maintenance, never resets a failed run.
    (evidence/'source.py').write_bytes(Path(__file__).read_bytes())
    env=dict(os.environ)
    env.update(DEER_FLOW_HOME=str(B/'.deer-flow'),
        DEER_FLOW_SOURCE_ACCOUNT_CONFIG=str(R/'host.json'),DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256=host.pin,
        GATEWAY_CORS_ORIGINS='https://fantastic-space-system-vprp5995g5q43xgv4-8001.app.github.dev',
        GATEWAY_CODESPACE_AUTH_PROXY_ORIGIN='https://localhost:8001',
        XJ_AGENT_MANAGEMENT_CONSENT_ENABLED='true',XJ_FIXED_AGENT_RUNTIME_CONSENT_ENABLED='true')
    gateway=manager=None
    def launch(argv,name):
        fd=os.open(evidence/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as output:
            return subprocess.Popen(argv,cwd=B,env=env,stdin=subprocess.DEVNULL,stdout=output,
                stderr=output,start_new_session=True)
    try:
        host.check('read')
        gateway=launch([str(B/'.venv/bin/python'),'-B','-m','uvicorn','app.gateway.app:app',
            '--host','127.0.0.1','--port','8001'],'gateway.log')
        opener=build_opener(ProxyHandler({}))
        def status(path):
            try:
                with opener.open('http://127.0.0.1:8001'+path,timeout=2) as response:return response.status
            except HTTPError as error:return error.code
        deadline=time.monotonic()+30
        while True:
            if gateway.poll() is not None:raise RuntimeError('ORIGINAL_GATEWAY_EXITED')
            try:
                if status('/health')==200:break
            except OSError:pass
            if time.monotonic()>deadline:raise RuntimeError('ORIGINAL_GATEWAY_NOT_HEALTHY')
            time.sleep(.25)
        if (status('/api/v1/auth/me')!=401 or status('/api/v1/auth/agent-management-consent')!=200
                or status('/api/v1/auth/agent-runtime-consent')!=200):raise RuntimeError('ORIGINAL_AUTH_BOUNDARY_CHANGED')
        lock=os.open(R/'supervisor.lock',os.O_RDWR|os.O_NOFOLLOW)
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(lock)
        argv=list(old);argv[1]=str(S/'platform_service_manager.py');argv[argv.index('--gateway-pid')+1]=str(gateway.pid)
        manager=launch(argv,'manager.log')
        time.sleep(3)
        if gateway.poll() is not None or manager.poll() is not None:raise RuntimeError('ORIGINAL_RELOAD_EXITED')
        if any(p.read_bytes()!=raw for p,raw in pins.items()):raise RuntimeError('ORIGINAL_AUTH_CONFIG_CHANGED')
        host.check('read')
        record=dict(status='ORIGINAL_SERVICE_RESUMED_VERIFY_HEARTBEAT',gateway_pid=gateway.pid,
            manager_pid=manager.pid,generation=host.config['generation'],pins_unchanged=True,
            idle_counts=counts,grant_issued=False,grant_extended=False,model_calls=0,observed_at=time.time())
        (evidence/'receipt.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
    except BaseException:
        for proc in (manager,gateway):
            if proc is not None and proc.poll() is None:
                proc.terminate();proc.wait(timeout=20)
        raise

if __name__=='__main__':main()
