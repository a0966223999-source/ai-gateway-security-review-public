"""Activate only the tested pending-cancel delta on the existing idle worker."""
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
    paths = ('fresh_batch_host.py', 'fresh_batch_rpc.py', 'platform_pending_cancel.py')
    baseline = '2cabd8501ed8cce4ec9d45597fc884a72fb4a004'
    revision = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    assert subprocess.check_output(['git','-C',str(destination),'rev-parse','HEAD'],text=True).strip() == baseline
    for name in paths[:2]:
        original = subprocess.check_output(['git','-C',str(destination),'show',baseline+':'+name])
        assert (destination/name).read_bytes() == original, 'LIVE_FILE_CHANGED'
    assert not (destination/paths[2]).exists(), 'PENDING_CANCEL_ALREADY_STAGED'
    host_root = Path('/home/codespace/.deer-platform-service-20261112')
    assert not (host_root/'STOP').exists(), 'HOST_STOPPED'
    config_bytes = (host_root/'host.json').read_bytes()
    assert hashlib.sha256(config_bytes).hexdigest() == (host_root/'config.sha256').read_text().strip()
    config = json.loads(config_bytes)
    assert config['generation'] == 'f8a69fac5f1b2acd07918b0afe7348c5'
    with sqlite3.connect((backend/'.deer-flow/data/deerflow.db').as_uri()+'?mode=ro',uri=True) as db:
        active = db.execute("SELECT count(*) FROM subagent_batch_items WHERE status IN ('queued','leased','running') AND batch_id IN (SELECT id FROM subagent_batches WHERE thread_id=? AND user_id=?)", (config['parent_id'],config['owner'])).fetchone()[0]
    assert active == 0, 'ACTIVE_ITEMS_PRESENT_NO_RELOAD'
    script = str(destination/'fresh_batch_worker.py').encode()
    workers = []
    for directory in Path('/proc').iterdir():
        if directory.name.isdigit():
            try:
                raw = (directory/'cmdline').read_bytes()
            except (OSError,ProcessLookupError):
                continue
            if script in raw.split(b'\0'):
                workers.append((int(directory.name),raw))
    assert len(workers) == 1, 'EXACTLY_ONE_ORIGINAL_WORKER_REQUIRED'
    pid, argv = workers[0]
    checkpoint = destination/('pending-cancel-rollout-'+str(time.time_ns()))
    checkpoint.mkdir(mode=0o700)
    hashes = {}
    for name in paths:
        target = destination/name
        if target.exists():
            (checkpoint/(name+'.before')).write_bytes(target.read_bytes())
        data = (source/name).read_bytes()
        hashes[name] = hashlib.sha256(data).hexdigest()
        staged = checkpoint/name
        staged.write_bytes(data)
        os.replace(staged,target)
    assert Path('/proc',str(pid),'cmdline').read_bytes() == argv, 'WORKER_CHANGED_BEFORE_SIGNAL'
    os.kill(pid,signal.SIGTERM)
    report = {'schema':'deer-pending-cancel-activation-v1','source_revision':revision,
              'baseline_revision':baseline,'installed_sha256':hashes,'signalled_worker_pid':pid,
              'active_items_before_reload':active,'config_authority_unchanged':True,
              'status':'INSTALLED_RESTART_REQUESTED_NOT_YET_VERIFIED'}
    (checkpoint/'activation.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__ == '__main__':
    main()
