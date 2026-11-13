"""Stage/test then pin-guarded update of the existing worker. No new service."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time

SERVICE=Path('/home/codespace/deer-platform-service-20261112')
ROOT=Path('/home/codespace/.deer-platform-service-20261112')
BACKEND=Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
WORK=Path('/home/codespace/dag-host-20261113')
NAMES=['fresh_batch_host.py','platform_codex_batch.py','platform_codex_executor.py']


def sha(raw):return hashlib.sha256(raw).hexdigest()


def process_rows():
    rows=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            argv=[v.decode() for v in (p/'cmdline').read_bytes().split(b'\0') if v]
            if len(argv)<2 or argv[1] not in [str(SERVICE/'fresh_batch_worker.py'),str(SERVICE/'platform_service_manager.py')]:continue
            st=(p/'stat').read_text().split(') ',1)[1].split()
            rows.append(dict(pid=int(p.name),parent=int(st[1]),start=st[19],argv=argv))
        except (OSError,ValueError):continue
    return rows


def same(row):
    return row in process_rows()


def idle():
    database=BACKEND/'.deer-flow/data/deerflow.db'
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
        counts=dict(db.execute('select status,count(*) from subagent_batch_items group by status'))
    if any(counts.get(k,0) for k in ('pending','queued','leased','running')):
        raise RuntimeError('ORIGINAL_SQL_NOT_IDLE_STOP')
    return counts


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true');args=parser.parse_args()
    src=Path(__file__).parent
    if not args.install:
        WORK.mkdir(mode=0o700)
        stage=WORK/'candidate';stage.mkdir(mode=0o700)
        for p in SERVICE.glob('*.py'):shutil.copyfile(p,stage/p.name)
        pins={n:sha((SERVICE/n).read_bytes()) for n in NAMES}
        patches=json.loads((src/'dag-patches.json').read_text())
        for n in NAMES:
            data=(stage/n).read_text()
            for patch in patches[n]:
                if data.count(patch['old'])!=1:raise RuntimeError('SOURCE_PATCH_MISMATCH:'+n)
                data=data.replace(patch['old'],patch['new'])
            (stage/n).write_text(data)
        shutil.copyfile(src/'platform_codex_dag.py',stage/'platform_codex_dag.py')
        tests=stage/'tests';tests.mkdir()
        for n in ['test_platform_pending_cancel.py','test_platform_codex_dag.py','test_platform_codex_batch_sql.py']:
            shutil.copyfile(src/'tests'/n,tests/n)
        env=dict(os.environ,XJ_DEER_BACKEND=str(BACKEND),PYTHONPATH=str(stage)+':'+str(tests))
        with (WORK/'tests.log').open('w') as log:
            result=subprocess.run([str(BACKEND/'.venv/bin/python'),'-m','unittest',
                'test_platform_codex_dag','test_platform_codex_batch_sql','-v'],
                cwd=stage,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=120)
        (WORK/'tests.exit').write_text(str(result.returncode))
        record=dict(status='DAG_STAGE_TESTED' if result.returncode==0 else 'DAG_TEST_FAILED',
            tests_exit=result.returncode,before=pins,after={n:sha((stage/n).read_bytes()) for n in NAMES},
            module_sha256=sha((stage/'platform_codex_dag.py').read_bytes()),observed_at=time.time())
        (WORK/'stage.json').write_text(json.dumps(record,indent=2))
        print(json.dumps(record));print((WORK/'tests.log').read_text()[-10000:])
        raise SystemExit(result.returncode)
    record=json.loads((WORK/'stage.json').read_text());stage=WORK/'candidate'
    if record['tests_exit']!=0 or (WORK/'tests.exit').read_text()!='0':raise RuntimeError('DAG_TESTS_NOT_PASS')
    if (SERVICE/'platform_codex_dag.py').exists():raise RuntimeError('DAG_MODULE_ALREADY_EXISTS_STOP')
    for n in NAMES:
        if sha((SERVICE/n).read_bytes())!=record['before'][n] or sha((stage/n).read_bytes())!=record['after'][n]:
            raise RuntimeError('SOURCE_CHANGED_STOP:'+n)
    if sha((stage/'platform_codex_dag.py').read_bytes())!=record['module_sha256']:raise RuntimeError('MODULE_CHANGED_STOP')
    config={n:(ROOT/n).read_bytes() for n in ['host.json','config.sha256']}
    cfg=json.loads(config['host.json'])
    if cfg['generation']!='f8a69fac5f1b2acd07918b0afe7348c5':raise RuntimeError('HOST_GENERATION_CHANGED_STOP')
    counts=idle();rows=process_rows()
    workers=[r for r in rows if r['argv'][1].endswith('/fresh_batch_worker.py')]
    managers=[r for r in rows if r['argv'][1].endswith('/platform_service_manager.py')]
    if len(workers)!=1 or len(managers)!=1 or workers[0]['parent']!=managers[0]['pid']:
        raise RuntimeError('ORIGINAL_WORKER_MANAGER_NOT_UNIQUE')
    worker,manager=workers[0],managers[0]
    backup=WORK/'before';backup.mkdir(mode=0o700)
    original={n:(SERVICE/n).read_bytes() for n in NAMES}
    for n,raw in original.items():(backup/n).write_bytes(raw)
    (SERVICE/'platform_codex_dag.py').write_bytes((stage/'platform_codex_dag.py').read_bytes())
    try:
        idle()
        if not same(worker) or not same(manager):raise RuntimeError('ORIGINAL_PROCESS_CHANGED_STOP')
        for n in NAMES:
            if sha((SERVICE/n).read_bytes())!=record['before'][n]:raise RuntimeError('SOURCE_CHANGED_DURING_INSTALL')
            temp=SERVICE/(n+'.dag-new');temp.write_bytes((stage/n).read_bytes());os.replace(temp,SERVICE/n)
        if any((ROOT/n).read_bytes()!=raw for n,raw in config.items()):raise RuntimeError('HOST_CONFIG_CHANGED')
        idle()
        if not same(worker) or not same(manager):raise RuntimeError('ORIGINAL_PROCESS_CHANGED_BEFORE_RELOAD')
        os.kill(worker['pid'],signal.SIGTERM)
        replacement=None
        for _ in range(80):
            candidates=[r for r in process_rows() if r['parent']==manager['pid'] and r['argv']==worker['argv'] and r['pid']!=worker['pid']]
            if len(candidates)==1:replacement=candidates[0];break
            time.sleep(.25)
        if replacement is None:raise RuntimeError('ORIGINAL_MANAGER_RELOAD_NOT_OBSERVED')
        time.sleep(2)
        if not same(replacement):raise RuntimeError('ORIGINAL_WORKER_RELOAD_EXITED')
        if any((ROOT/n).read_bytes()!=raw for n,raw in config.items()):raise RuntimeError('HOST_CONFIG_CHANGED')
        receipt=dict(status='ORIGINAL_HOST_DAG_INSTALLED',old_worker=worker['pid'],worker=replacement['pid'],
            manager=manager['pid'],generation=cfg['generation'],config_unchanged=True,idle_counts=counts,
            source_sha256={n:sha((SERVICE/n).read_bytes()) for n in NAMES+['platform_codex_dag.py']},
            model_calls=0,observed_at=time.time())
        (WORK/'install.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt))
    except BaseException:
        # Only restore files still carrying our candidate, never another writer's changes.
        for n,raw in original.items():
            if sha((SERVICE/n).read_bytes())==record['after'][n]:(SERVICE/n).write_bytes(raw)
        new=SERVICE/'platform_codex_dag.py'
        if new.exists() and sha(new.read_bytes())==record['module_sha256']:new.unlink()
        if same(manager):
            for current in process_rows():
                if current['parent']==manager['pid'] and current['argv']==worker['argv'] and same(current):
                    os.kill(current['pid'],signal.SIGTERM)
        raise


if __name__=='__main__':main()
