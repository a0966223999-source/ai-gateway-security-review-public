"""Install the tested single-item-stop delta on the original idle worker only."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time


def main():
    source = Path(__file__).resolve().parents[1]
    destination = Path('/home/codespace/deer-platform-service-20261112')
    backend = Path('/home/codespace/gateway-r12-restore-yfl4xryg/source/backend')
    root = Path('/home/codespace/.deer-platform-service-20261112')
    baseline = '23c87bb16464d13b2f8e8c929c14027c26898401'
    paths = ('fresh_batch_host.py','fresh_batch_rpc.py','fresh_batch_worker.py',
             'platform_codex_executor.py','platform_codex_stop.py')
    revision = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    for name in paths[:-1]:
        expected = subprocess.check_output(['git','-C',str(destination),'show',baseline+':'+name])
        assert (destination/name).read_bytes() == expected, 'LIVE_FILE_CHANGED:'+name
    assert not (destination/paths[-1]).exists(), 'STOP_DELTA_ALREADY_INSTALLED'
    assert not (root/'STOP').exists(), 'HOST_STOPPED'
    config_bytes = (root/'host.json').read_bytes()
    assert hashlib.sha256(config_bytes).hexdigest() == (root/'config.sha256').read_text().strip()
    config = json.loads(config_bytes)
    assert config['generation'] == 'f8a69fac5f1b2acd07918b0afe7348c5'
    with sqlite3.connect((backend/'.deer-flow/data/deerflow.db').as_uri()+'?mode=ro',uri=True) as db:
        active = db.execute("SELECT count(*) FROM subagent_batch_items WHERE status IN ('queued','leased','running') AND batch_id IN (SELECT id FROM subagent_batches WHERE thread_id=? AND user_id=?)",(config['parent_id'],config['owner'])).fetchone()[0]
    assert active == 0, 'ACTIVE_ITEMS_PRESENT_NO_RELOAD'
    workers = []
    script = str(destination/'fresh_batch_worker.py').encode()
    for directory in Path('/proc').iterdir():
        if directory.name.isdigit():
            try: raw = (directory/'cmdline').read_bytes()
            except OSError: continue
            if script in raw.split(b'\0'): workers.append((int(directory.name),raw))
    assert len(workers) == 1, 'ONE_ORIGINAL_WORKER_REQUIRED'
    pid,argv = workers[0]
    checkpoint = destination/('item-stop-rollout-'+str(time.time_ns()))
    checkpoint.mkdir(mode=0o700)
    hashes = {}
    for name in paths:
        target = destination/name
        if target.exists(): (checkpoint/(name+'.before')).write_bytes(target.read_bytes())
        data = (source/name).read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        staged = checkpoint/name
        staged.write_bytes(data)
        os.replace(staged,target)
    assert Path('/proc',str(pid),'cmdline').read_bytes() == argv, 'WORKER_CHANGED'
    os.kill(pid,signal.SIGTERM)
    report = {'schema':'deer-single-item-stop-activation-v1','revision':revision,
              'previous_revision':baseline,'installed_sha256':hashes,'signalled_worker_pid':pid,
              'active_items_before_reload':active,'authority_unchanged':True,
              'status':'INSTALLED_RESTART_REQUESTED_NOT_YET_VERIFIED'}
    (checkpoint/'activation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':main()
