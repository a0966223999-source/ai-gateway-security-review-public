"""Read-only original Deer AIO rollout preflight and configuration rendering.

Never imports/constructs a provider: AIO construction starts background
reconciliation and may destroy or adopt containers. Never starts/pulls/stops
containers, changes host permissions, or reads credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ORIGINAL_REVISION = '345f08be00c8a9495079b732a39b46aa9af1584e'
FILES = (
    'sandbox/sandbox.py', 'sandbox/sandbox_provider.py', 'sandbox/identity.py',
    'community/aio_sandbox/aio_sandbox_provider.py',
    'community/aio_sandbox/aio_sandbox.py', 'community/aio_sandbox/backend.py',
    'community/aio_sandbox/local_backend.py', 'community/aio_sandbox/ownership.py',
    'community/aio_sandbox/network_proxy.py', 'community/warm_pool_lifecycle.py',
)
DIGEST_IMAGE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._:/-]*@sha256:[0-9a-f]{64}$')


def query(argv: list[str]) -> dict:
    try:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=8, check=False)
        return {'exit_code': result.returncode, 'stdout': result.stdout[:65536],
                'stderr_present': bool(result.stderr)}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'exit_code': None, 'error_type': type(error).__name__, 'stdout': ''}


def inspect_host(backend: Path) -> dict:
    source_root = backend / 'packages/harness/deerflow'
    sources = {}
    for name in FILES:
        path = source_root / name
        # Do not follow a substituted source symlink or load product Python.
        sources[name] = {'present': path.is_file() and not path.is_symlink()}
        if sources[name]['present']:
            sources[name]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    docker = shutil.which('docker')
    report = {'schema': 'deer-aio-host-preflight-v1', 'backend': str(backend),
              'reference_revision': ORIGINAL_REVISION, 'source_files': sources,
              'docker_cli_present': docker is not None,
              'agent_sandbox_sdk_present': importlib.util.find_spec('agent_sandbox') is not None,
              'provider_constructed': False, 'containers_started': 0,
              'configuration_applied': False, 'isolation_verified': False}
    if docker is None:
        return dict(report, status='BLOCKED_NO_DOCKER_CLI')
    server = query([docker, 'version', '--format', '{{json .Server.Version}}'])
    report['docker_server'] = server
    if server['exit_code'] != 0:
        return dict(report, status='BLOCKED_DOCKER_DAEMON')
    images = query([docker, 'image', 'ls', '--digests', '--no-trunc', '--format',
                    '{{.Repository}}|{{.Tag}}|{{.Digest}}|{{.ID}}'])
    report['image_query_exit_code'] = images['exit_code']
    report['cached_images'] = []
    if images['exit_code'] == 0:
        for line in images['stdout'].splitlines():
            parts = line.split('|')
            if len(parts) == 4 and any(s in parts[0].lower() for s in ('sandbox', 'deer-flow')):
                report['cached_images'].append(dict(zip(('repository', 'tag', 'digest', 'id'), parts)))
    report['existing_deer_containers'] = query([docker, 'ps', '--filter',
        'name=deer-flow-sandbox', '--format', '{{.Names}}|{{.Status}}'])
    missing = [name for name, value in sources.items() if not value['present']]
    report['missing_source_files'] = missing
    if missing or not report['agent_sandbox_sdk_present'] or images['exit_code'] != 0:
        return dict(report, status='BLOCKED_ORIGINAL_DEPENDENCIES')
    # This is never a launch permission or a verified isolation result.
    return dict(report, status='PRECHECK_COLLECTED_NOT_ADMITTED')


def render_candidate(report: dict, *, image: str, proxy_image: str) -> dict:
    if (report.get('schema') != 'deer-aio-host-preflight-v1'
            or report.get('status') != 'PRECHECK_COLLECTED_NOT_ADMITTED'
            or report.get('docker_server', {}).get('exit_code') != 0
            or set(report.get('source_files', {})) != set(FILES)
            or any(not value.get('present') for value in report['source_files'].values())
            or not report.get('agent_sandbox_sdk_present')):
        raise ValueError('ORIGINAL_AIO_PREFLIGHT_INCOMPLETE')
    if any(not DIGEST_IMAGE.fullmatch(value) for value in (image, proxy_image)):
        raise ValueError('PINNED_AIO_AND_PROXY_IMAGE_DIGESTS_REQUIRED')
    cached = {value['repository'] + '@' + value['digest'] for value in report.get('cached_images', [])}
    if image not in cached or proxy_image not in cached:
        raise ValueError('VERIFIED_IMAGES_NOT_CACHED_NO_AUTOMATIC_PULL')
    return {
        'status': 'CANDIDATE_NOT_APPLIED_NOT_LAUNCH_AUTHORITY',
        'single_instance_test_only': True,
        'sandbox_fragment': {
            'use': 'deerflow.community.aio_sandbox:AioSandboxProvider',
            'allow_host_bash': False, 'image': image,
            'replicas': 1, 'container_prefix': 'deer-flow-sandbox-rollout-20261112',
            'idle_timeout': 60, 'mounts': [], 'environment': {},
            'network': {'mode': 'isolated', 'allow_domains': [], 'approval': 'deny',
                        'proxy_image': proxy_image},
            'ownership': {'type': 'memory'},
        },
        'test_process_environment': {
            'DEER_FLOW_SANDBOX_BIND_HOST': '127.0.0.1',
            'DEER_FLOW_SANDBOX_SECCOMP_UNCONFINED': '0',
            'DEER_FLOW_SANDBOX_MEMORY': '2g', 'DEER_FLOW_SANDBOX_CPUS': '1',
            'DEER_FLOW_SANDBOX_PIDS_LIMIT': '256',
        },
        'unverified': ['image startup under builtin seccomp', 'mount scope',
            'same-scope reuse', 'cross-user isolation', 'egress denial',
            'command timeout and child-process stop', 'destroy/readback',
            'existing trusted task/caller authorization and budget'],
        'notes': ['Merge only in a dedicated bounded test process, never overwrite live config.',
                  'release parks a container; destroy plus independent readback proves cleanup.',
                  'Multiple Gateway instances must use the original shared ownership store.',
                  'AIO ignores the per-command timeout argument; an outer manager must stop it.'],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--backend', required=True, type=Path)
    render = sub.add_parser('render')
    render.add_argument('--preflight', required=True, type=Path)
    render.add_argument('--image', required=True)
    render.add_argument('--proxy-image', required=True)
    args = parser.parse_args()
    if args.action == 'inspect':
        result = inspect_host(args.backend.resolve(strict=True))
    else:
        result = render_candidate(json.loads(args.preflight.read_text()),
                                  image=args.image, proxy_image=args.proxy_image)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
