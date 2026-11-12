"""Resume the exact installed original services after Codespace suspension.
No code/config/key/grant changes; no replay, renewal or model invocation.
"""
from pathlib import Path
import hashlib,json,os,stat,subprocess,time
from urllib.request import build_opener,ProxyHandler
from urllib.error import HTTPError
B=Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
S=Path('/home/codespace/deer-platform-service-20261112')
R=Path('/home/codespace/.deer-platform-service-20261112')
D=Path('/home/codespace/dr-management-host-411272e8')
ENDPOINT='https://ai-gateway-security-review-public-hy1ysqmpn-yoyo-5560.vercel.app/internal/deer-batch/rpc'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    guards={"fresh_batch_host.py":"23cd919dd4c5e3be89145e02fd956a4239efd76f","fresh_batch_worker.py":"ad08bfe9e75ba09ebdc8753e666412287e9c0146","platform_service_manager.py":"f812b44525a963334cc6764df19a2e254c945bac","deer_management_rpc.py":"e09c2ff815d00ba00bcdd2f45fb5e4c2a294df59","worker_signal_lifecycle.py":"7915c41b5eaf5011f7311b77f6b951958391eff8","deer_management_host.py":"35374ed211b2ae552354dcf02a2ae6d2432b2fa8","deer_management_consent.py":"075522fdabb8609592fe422828093ae3d6fe1292","deer_management_journal.py":"a6d8e70d0b3c363c4b35ca21c36a07e2691eb3c9","deer_agent_management.py":"445d93f74093dec21f2f30d38d03c03f041c5849","gateway_agent_consent.py":"c9d72e1884018e183fa6defb246f9a02df000f75","agent_management_consent.html":"eaaf67c26aa4cb1f8ce544e315deec13dd5f3fa4"}
    def checked(path,pin):
        raw=path.read_bytes()
        assert hashlib.sha1(b'blob '+str(len(raw)).encode()+bytes([0])+raw).hexdigest()==pin,'ORIGINAL_BLOB_CHANGED:'+path.name
    for name in ('fresh_batch_host.py','fresh_batch_worker.py','platform_service_manager.py','deer_management_rpc.py','worker_signal_lifecycle.py','deer_management_host.py','deer_management_consent.py','deer_management_journal.py','deer_agent_management.py'):
        checked(S/name,guards[name])
    e=R/'dr-consent-page-install-v1'
    before=(e/'app.py.before').read_bytes()
    assert sha(before)=='55dacb6c1af0d8881bba983be3385fef3327d83775f06bc1945ff44eab279faa','ORIGINAL_APP_BASE_CHANGED'
    anchor='    install_account_link_routes(app)'
    expected=before.decode().replace(anchor,anchor+'\n    from gateway_agent_consent import install_gateway_management_consent\n    from os import environ as management_environment\n    install_gateway_management_consent(app, enabled=management_environment.get("XJ_AGENT_MANAGEMENT_CONSENT_ENABLED") == "true")')
    assert before.decode().count(anchor)==1 and (B/'app/gateway/app.py').read_text()==expected,'CURRENT_GATEWAY_APP_CHANGED'
    for name in ('deer_agent_management.py','deer_management_consent.py','gateway_agent_consent.py','agent_management_consent.html'):
        checked(B/name,guards[name])
    host=(R/'host.json').read_bytes()
    assert sha(host)=='66dd5d073a9a92f957307fe69d7c3cc642edef13b10893943fd914ba0c2d127f','ORIGINAL_HOST_PIN_CHANGED'
    assert not (R/'STOP').exists() and not (R/'STOP').is_symlink(),'ORIGINAL_SERVICE_STOPPED'
    config=json.loads(host)
    assert config['generation']=='f8a69fac5f1b2acd07918b0afe7348c5','GENERATION_CHANGED'
    for p in Path('/proc').glob('[0-9]*/cmdline'):
        try:a=p.read_bytes().split(bytes([0]))
        except OSError:continue
        assert b'app.gateway.app:app' not in a and str(S/'fresh_batch_worker.py').encode() not in a and str(S/'platform_service_manager.py').encode() not in a,'EXISTING_ORIGINAL_PROCESS'
    secret=B/'.deer-flow/.jwt_secret';info=secret.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,'PERSISTED_SECRET_NOT_PRIVATE'
    receipt=json.loads((R/'dr-management-pending-host-v1/receipt.json').read_text())
    pending=receipt['pending_grant_id']
    assert len(pending)==64 and all(c in '0123456789abcdef' for c in pending),'PENDING_SELECTOR_INVALID'
    env=dict(os.environ)
    assert env.get('DEER_FLOW_AUTH_DISABLED','').lower() not in ('1','true'),'AUTH_DISABLED'
    env.update(DEER_FLOW_HOME=str(B/'.deer-flow'),DEER_FLOW_SOURCE_ACCOUNT_CONFIG=str(R/'host.json'),
      DEER_FLOW_SOURCE_ACCOUNT_CONFIG_SHA256=sha(host),AUTH_JWT_SECRET=secret.read_text().strip(),
      GATEWAY_CORS_ORIGINS='https://fantastic-space-system-vprp5995g5q43xgv4-8001.app.github.dev',
      GATEWAY_CODESPACE_AUTH_PROXY_ORIGIN='https://localhost:8001',XJ_AGENT_MANAGEMENT_CONSENT_ENABLED='true')
    assert env['AUTH_JWT_SECRET'],'EMPTY_PERSISTED_SECRET'
    run=R/('codespace-resume-'+str(int(time.time())));run.mkdir(mode=0o700)
    started=[]
    def launch(argv,name):
        fd=os.open(run/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'ab') as log:
            p=subprocess.Popen(argv,cwd=B,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        started.append(p);return p
    client=build_opener(ProxyHandler({}))
    def status(path):
        try:
            with client.open('http://127.0.0.1:8001'+path,timeout=2) as r:return r.status
        except HTTPError as e:return e.code
    try:
        gateway=launch([str(B/'.venv/bin/python'),'-B','-m','uvicorn','app.gateway.app:app','--host','127.0.0.1','--port','8001'],'gateway.log')
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            assert gateway.poll() is None,'GATEWAY_START_FAILED'
            try:
                if status('/health')==200:break
            except OSError:pass
            time.sleep(.25)
        else:raise RuntimeError('GATEWAY_HEALTH_NOT_READY')
        assert status('/api/v1/auth/me')==401 and status('/api/v1/auth/agent-management-consent')==200,'ORIGINAL_AUTH_OR_REVIEW_CHANGED'
        manager=launch([str(B/'.venv/bin/python'),str(S/'platform_service_manager.py'),
          '--root',str(R),'--gateway-backend',str(B),'--database',str(B/'.deer-flow/data/deerflow.db'),
          '--endpoint',ENDPOINT,'--agent-management-grant-id',pending,'--gateway-pid',str(gateway.pid)],'manager.log')
        time.sleep(1);assert manager.poll() is None,'MANAGER_START_FAILED'
        assert (R/'host.json').read_bytes()==host,'HOST_CONFIG_CHANGED_DURING_START'
        result={'status':'ORIGINAL_SERVICES_RESUMED_VERIFY_SIGNED_HEARTBEAT','gateway_pid':gateway.pid,'manager_pid':manager.pid,'health':200,'anonymous_me':401,'generation':config['generation'],'new_credentials':False,'grant_created_or_renewed':False,'source_replayed':False,'run_directory':str(run)}
        (run/'receipt.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
    except BaseException:
        for p in reversed(started):
            if p.poll() is None:p.terminate();p.wait(timeout=10)
        raise
if __name__=='__main__':main()
