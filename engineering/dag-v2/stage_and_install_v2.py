"""Test a pinned candidate, then update only the original idle worker modules."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time

B=Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
R=Path('/home/codespace/.deer-platform-service-20261112')
S=Path('/home/codespace/deer-platform-service-20261112')
W=Path('/home/codespace/dag-v2-host-20261113')
CHANGES=['platform_codex_dag.py','platform_dag_runtime.py','fresh_batch_host.py','fresh_batch_worker.py']

def sha(raw):return hashlib.sha256(raw).hexdigest()

def processes():
    result=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            argv=[x.decode() for x in (p/'cmdline').read_bytes().split(b'\0') if x]
            if len(argv)<2 or argv[1] not in [str(S/'fresh_batch_worker.py'),str(S/'platform_service_manager.py')]:continue
            st=(p/'stat').read_text().split(') ',1)[1].split()
            result.append(dict(pid=int(p.name),parent=int(st[1]),start=st[19],argv=argv))
        except OSError:continue
    return result

def source_pins():return {p.name:sha(p.read_bytes()) for p in S.glob('*.py')}

def snapshot(db):
    counts=dict(db.execute('select status,count(*) from subagent_batch_items group by status'))
    if any(n for k,n in counts.items() if k not in {'succeeded','failed','cancelled'}):
        raise RuntimeError('ORIGINAL_SQL_NOT_IDLE')
    values=[db.execute('select * from '+name+' order by id').fetchall()
        for name in ['subagent_batches','subagent_batch_items']]
    return dict(counts=counts,sha256=sha(json.dumps(values,sort_keys=True,separators=(',',':'),allow_nan=False).encode()))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--stage');parser.add_argument('--install',action='store_true');args=parser.parse_args()
    stage=W/'candidate'
    if args.stage:
        package=json.loads(Path(args.stage).read_text());W.mkdir(mode=0o700);stage.mkdir(mode=0o700)
        before=source_pins()
        for name,expected in package['expected_before'].items():
            if before.get(name)!=expected:raise RuntimeError('ORIGINAL_BASELINE_CHANGED:'+name)
        backend_pins={name:sha((B/'packages/harness/deerflow/persistence/subagent_batches'/name).read_bytes())
            for name in ['model.py','sql.py']}
        if backend_pins!=package['backend_sha256']:raise RuntimeError('ORIGINAL_SQL_CONTRACT_CHANGED')
        for p in S.glob('*.py'):shutil.copyfile(p,stage/p.name)
        if (S/'tests').exists():shutil.copytree(S/'tests',stage/'tests',ignore=shutil.ignore_patterns('__pycache__'))
        else:(stage/'tests').mkdir()
        for name,content in package['files'].items():
            if name not in CHANGES+['fresh_batch_rpc.py','INDEPENDENT_QA.md'] and not (name.startswith('tests/test_') and name.endswith('.py') and '/' not in name[6:]):
                raise RuntimeError('CANDIDATE_PATH_NOT_ALLOWED')
            if name.endswith('.py'):ast.parse(content)
            (stage/name).write_text(content)
        env=dict(os.environ,XJ_DEER_BACKEND=str(B),PYTHONPATH=str(stage)+':'+str(stage/'tests'))
        with (W/'tests.log').open('xb') as log:
            completed=subprocess.run([str(B/'.venv/bin/python'),'-m','unittest',
                'test_dag_v2','test_platform_codex_dag','test_platform_codex_batch_sql','test_platform_pending_cancel','-q'],
                cwd=stage,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        record=dict(status='STAGED_PASS' if completed.returncode==0 else 'STAGED_FAIL',
            tests_exit=completed.returncode,before=before,backend_sha256=backend_pins,
            after={n:sha((stage/n).read_bytes()) for n in CHANGES},package_sha256=sha(Path(args.stage).read_bytes()))
        (W/'stage.json').write_text(json.dumps(record,indent=2));print(json.dumps(record))
        print((W/'tests.log').read_text()[-1200:]);raise SystemExit(completed.returncode)
    if not args.install:raise RuntimeError('EXPLICIT_STAGE_OR_INSTALL_REQUIRED')
    record=json.loads((W/'stage.json').read_text())
    if record['tests_exit']!=0 or record['before']!=source_pins():raise RuntimeError('STAGE_FAILED_OR_SOURCE_CHANGED')
    if (R/'STOP').exists():raise RuntimeError('HOST_STOPPED')
    cfg=json.loads((R/'host.json').read_text())
    if cfg['generation']!='f8a69fac5f1b2acd07918b0afe7348c5':raise RuntimeError('HOST_GENERATION_CHANGED')
    pins={p:p.read_bytes() for p in (R/'host.json',R/'config.sha256',B/'.deer-flow/.jwt_secret')}
    rows=processes();workers=[p for p in rows if p['argv'][1]==str(S/'fresh_batch_worker.py')]
    managers=[p for p in rows if p['argv'][1]==str(S/'platform_service_manager.py')]
    if len(workers)!=1 or len(managers)!=1 or workers[0]['parent']!=managers[0]['pid']:
        raise RuntimeError('ORIGINAL_WORKER_MANAGER_NOT_UNIQUE')
    worker,manager=workers[0],managers[0]
    original={n:(S/n).read_bytes() if (S/n).exists() else None for n in CHANGES}
    for n in CHANGES:
        if sha((stage/n).read_bytes())!=record['after'][n]:raise RuntimeError('CANDIDATE_CHANGED')
    backup=W/'before';backup.mkdir(mode=0o700)
    for n,raw in original.items():
        if raw is not None:(backup/n).write_bytes(raw)
    # Fence SQL writers before the final idle check, so a new native send cannot
    # race the maintenance stop. This transaction changes no stored values.
    db=sqlite3.connect(str(B/'.deer-flow/data/deerflow.db'),timeout=2,isolation_level=None)
    modified=False
    try:
        db.execute('BEGIN IMMEDIATE');before=snapshot(db)
        if source_pins()!=record['before'] or worker not in processes() or manager not in processes():
            raise RuntimeError('ORIGINAL_STATE_CHANGED')
        for n in CHANGES:
            raw=(stage/n).read_bytes();tmp=S/(n+'.dag-v2-install')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
            os.replace(tmp,S/n);modified=True
        if any(p.read_bytes()!=raw for p,raw in pins.items()):raise RuntimeError('ORIGINAL_AUTH_PIN_CHANGED')
        os.kill(worker['pid'],signal.SIGTERM)
        deadline=time.monotonic()+15
        while worker in processes():
            if time.monotonic()>deadline:raise RuntimeError('ORIGINAL_WORKER_DRAIN_NOT_OBSERVED')
            time.sleep(.1)
        if snapshot(db)!=before:raise RuntimeError('SQL_CHANGED_DURING_MAINTENANCE')
        db.rollback();db.close();db=None
        replacement=None
        replacement_deadline=time.monotonic()+15
        while time.monotonic()<replacement_deadline:
            candidates=[p for p in processes() if p['parent']==manager['pid'] and p['argv']==worker['argv'] and p['pid']!=worker['pid']]
            if len(candidates)==1:replacement=candidates[0];break
            time.sleep(.25)
        if replacement is None:raise RuntimeError('ORIGINAL_MANAGER_RELOAD_NOT_OBSERVED')
        time.sleep(2)
        if replacement not in processes() or manager not in processes():raise RuntimeError('ORIGINAL_RELOAD_EXITED')
        if any(p.read_bytes()!=raw for p,raw in pins.items()):raise RuntimeError('ORIGINAL_AUTH_PIN_CHANGED')
        receipt=dict(status='ORIGINAL_DAG_V2_RUNTIME_INSTALLED',old_worker=worker['pid'],worker=replacement['pid'],
            manager=manager['pid'],generation=cfg['generation'],pins_unchanged=True,original_sql=before,
            source_sha256={n:sha((S/n).read_bytes()) for n in CHANGES},observed_at=time.time())
        (W/'install.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
    except BaseException:
        if db is not None:db.rollback();db.close();db=None
        if modified:
            for n,raw in original.items():
                if (S/n).exists() and sha((S/n).read_bytes())==record['after'][n]:
                    if raw is None:(S/n).unlink()
                    else:
                        tmp=S/(n+'.rollback');tmp.write_bytes(raw);os.replace(tmp,S/n)
            for p in processes():
                if p['parent']==manager['pid'] and p['argv']==worker['argv'] and p in processes():
                    os.kill(p['pid'],signal.SIGTERM)
        raise
    finally:
        if db is not None:db.rollback();db.close()

if __name__=='__main__':main()
