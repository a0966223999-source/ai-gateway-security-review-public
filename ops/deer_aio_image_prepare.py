"""Fetch the official pinned AIO image with a bounded disk/time guard.

Does not construct a provider, start containers, restart Docker, prune data,
or change any live Gateway configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

IMAGE = ('ghcr.io/agent-infra/sandbox@sha256:'
         '9a597aaa3716aca2fd42a517ceecd41063e5ceedcef43eb68bf7c059c0128b7a')
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
FLOOR = 2 * 1024**3


def main() -> None:
    env = {k: v for k, v in os.environ.items() if k not in (
        'DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY',
        'DOCKER_CERT_PATH')}
    output = Path('/tmp/deer-aio-image-prepare.json')
    report = {'schema': 'deer-aio-image-prepare-v1', 'image': IMAGE,
              'started_at': time.time(), 'containers_started': 0,
              'configuration_applied': False, 'disk_floor_bytes': FLOOR}
    before = shutil.disk_usage('/var/lib/docker').free
    report['free_bytes_before'] = before
    if before < FLOOR:
        report['status'] = 'BLOCKED_DISK_RESERVE'
    else:
        # File-backed logs cannot fill RAM; only the fixed official image is pulled.
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([*DOCKER, 'pull', IMAGE], env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True)
            deadline = time.monotonic() + 900
            reason = None
            while process.poll() is None:
                if shutil.disk_usage('/var/lib/docker').free < FLOOR:
                    reason = 'BLOCKED_DISK_RESERVE'
                elif time.monotonic() >= deadline:
                    reason = 'BLOCKED_PULL_DEADLINE'
                if reason:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    break
                time.sleep(0.25)
            report['pull_exit_code'] = process.returncode
            log.seek(0)
            data = log.read()
            report['pull_log_sha256'] = hashlib.sha256(data).hexdigest()
            # Registry errors may include signed URLs; retain only fixed categories.
            report['pull_error_categories'] = [label for label, marker in (
                ('NO_SPACE', b'no space left'), ('REGISTRY_DENIED', b'denied'),
                ('TLS_ERROR', b'certificate'), ('NOT_FOUND', b'not found'))
                if marker in data.lower()]
            report['status'] = reason or ('IMAGE_CACHED' if process.returncode == 0
                                         else 'BLOCKED_PULL_ERROR')
        if report['status'] == 'IMAGE_CACHED':
            inspect = subprocess.run([*DOCKER, 'image', 'inspect', IMAGE],
                env=env, capture_output=True, text=True, timeout=15, check=False)
            report['inspect_exit_code'] = inspect.returncode
            if inspect.returncode == 0:
                value = json.loads(inspect.stdout)[0]
                report['image_id'] = value['Id']
                report['repo_digests'] = value.get('RepoDigests', [])
                report['image_size_bytes'] = value['Size']
                report['architecture'] = value['Architecture']
            else:
                report['status'] = 'BLOCKED_IMAGE_READBACK'
    report['free_bytes_after'] = shutil.disk_usage('/var/lib/docker').free
    report['finished_at'] = time.time()
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
