"""Restore the existing original Codespaces login proxy setting; retain all original gates."""
from pathlib import Path
import json
import os
import sys
import time

B=Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
S=Path('/home/codespace/deer-platform-service-20261112')
R=Path('/home/codespace/.deer-platform-service-20261112')
ORIGIN='https://fantastic-space-system-vprp5995g5q43xgv4-8001.app.github.dev'


def main():
    sys.path.insert(0,'/home/codespace/dr-consent-page-84a0640e/ops')
    from install_management_consent_page import process,stop,start,alive,idle,wait_health,status
    previous=json.loads((R/'dr-management-original-origin-v1/receipt.json').read_text())
    gateway=process(previous['gateway_pid'],'app.gateway.app:app')
    manager=process(previous['manager_pid'],str(S/'platform_service_manager.py'))
    if (gateway['env'].get('GATEWAY_CORS_ORIGINS') != ORIGIN or gateway['env'].get('GATEWAY_CODESPACE_AUTH_PROXY_ORIGIN')):
        raise RuntimeError('EXISTING_ORIGIN_CONFIG_QUERY_ONLY')
    counts=idle(B/'.deer-flow/data/deerflow.db')
    time.sleep(1)
    idle(B/'.deer-flow/data/deerflow.db')
    evidence=R/'dr-management-original-proxy-v1'
    evidence.mkdir(mode=0o700)
    (evidence/'executed-source.py').write_bytes(Path(__file__).read_bytes())
    new_gateway=None
    new_manager=None
    original_env=dict(gateway['env'])
    def manager_for(pid):
        argv=list(manager['argv'])
        argv[argv.index('--gateway-pid')+1]=str(pid)
        return {**manager,'argv':argv}
    try:
        stop(manager)
        if not alive(gateway):
            raise RuntimeError('ORIGINAL_GATEWAY_CHANGED_STOP')
        idle(B/'.deer-flow/data/deerflow.db')
        stop(gateway)
        gateway['env']={**original_env,'GATEWAY_CODESPACE_AUTH_PROXY_ORIGIN':'https://localhost:8001'}
        new_gateway=start(gateway,evidence/'gateway.log')
        wait_health()
        if status('/api/v1/auth/me')[0]!=401:
            raise RuntimeError('ANONYMOUS_BOUNDARY_CHANGED_STOP')
        new_manager=start(manager_for(new_gateway.pid),evidence/'manager.log')
        time.sleep(2)
        if new_manager.poll() is not None:
            raise RuntimeError('ORIGINAL_MANAGER_START_FAILED')
        receipt={'status':'ORIGINAL_CODESPACE_LOGIN_PROXY_RESTORED',
            'gateway_pid':new_gateway.pid,'manager_pid':new_manager.pid,
            'origin':ORIGIN,'auth_and_csrf_retained':True,'idle_counts':counts,
            'management_consent_issued':False,'model_executed':False,
            'observed_at':time.time()}
        (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2))
        print(json.dumps(receipt))
    except BaseException:
        for child in (new_manager,new_gateway):
            if child is not None and child.poll() is None:
                child.terminate()
                child.wait(timeout=20)
        if not alive(gateway):
            gateway['env']=original_env
            rollback=start(gateway,evidence/'rollback-gateway.log')
            wait_health()
            start(manager_for(rollback.pid),evidence/'rollback-manager.log')
        elif not alive(manager):
            start(manager,evidence/'rollback-manager.log')
        raise


if __name__=='__main__':
    main()
