"""Run fresh batch host against an EXISTING original Gateway SQLite database."""
import argparse
import asyncio
import os
from pathlib import Path
import sqlite3
import sys
import base64
import json
import secrets
import time
import signal
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener


async def serve(args):
    backend = Path(args.gateway_backend).resolve(strict=True)
    sys.path[:0] = [str(backend), str(backend / 'packages/harness'), str(backend / 'packages/extension-api')]
    database = Path(args.database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'subagent_batches', 'subagent_batch_items'} <= tables:
            raise ValueError('EXISTING_GATEWAY_BATCH_SCHEMA_REQUIRED')
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
    from fresh_batch_host import FreshBatchHost, canonical
    host = FreshBatchHost(args.root)
    management = None
    if args.agent_management_grant_id is not None:
        if args.gateway_pid is None or 'agent' not in host.config['actions']:
            raise ValueError('ORIGINAL_MANAGEMENT_GATEWAY_AND_ENROLLMENT_REQUIRED')
        from deer_management_host import original_gateway_management
        management = original_gateway_management(host, backend=backend,
            gateway_pid=args.gateway_pid, grant_id=args.agent_management_grant_id)
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != 'https' or not endpoint.hostname or not endpoint.hostname.endswith('.vercel.app')
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path != '/internal/deer-batch/rpc'):
        raise ValueError('PINNED_ORIGINAL_HTTPS_CONTROL_ENDPOINT_REQUIRED')
    os.chdir(backend)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(database), connect_args={'timeout': 5})
    repository = SubagentBatchRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        class RejectRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = build_opener(RejectRedirect())
        def control(action, payload):
            host.check('read')
            body = dict(domain='fresh-deer-batch-control-v1', generation=host.config['generation'],
                        action=action, nonce=secrets.token_hex(16), expires_at=time.time() + 30, payload=payload)
            envelope = dict(body=body, signature=base64.b64encode(host.key.sign(canonical(body))).decode())
            request = Request(args.endpoint, data=canonical(envelope), headers={'Content-Type': 'application/json'})
            # urllib follows no alternative host here: reject redirects.
            with opener.open(request, timeout=10) as response:
                if response.url != args.endpoint:
                    raise ValueError('CONTROL_REDIRECT_REJECTED')
                result = json.loads(response.read(131073))
            host.check('read')
            return result
        with host.single_worker():
            last_heartbeat = 0
            active = set()

            async def handle(envelope):
                try:
                    response = await host.execute(envelope, repository=repository,
                                                  agent_management=management)
                    await asyncio.to_thread(control, 'respond',
                        {'request_id': response['body']['request_id'], 'response': response})
                except Exception:
                    # Keep the durable request/attempt. Never replay after an
                    # unknown effect or pretend the original worker completed.
                    print('BATCH_REQUEST_QUERY_ONLY', flush=True)

            try:
                while host.check('read'):
                    if time.time() - last_heartbeat >= 10:
                        heartbeat = dict(domain='fresh-deer-batch-heartbeat-v1', generation=host.config['generation'],
                                         parent_id=host.config['parent_id'], observed_at=time.time())
                        signed = dict(body=heartbeat, signature=base64.b64encode(host.key.sign(canonical(heartbeat))).decode())
                        await asyncio.to_thread(control, 'heartbeat', signed)
                        last_heartbeat = time.time()
                    # Keep the single original consumer and bound request concurrency.
                    # Execution slots remain separately capped at two, before SQL reserve.
                    active = {task for task in active if not task.done()}
                    if len(active) >= 8:
                        await asyncio.wait(active, timeout=.25, return_when=asyncio.FIRST_COMPLETED)
                        continue
                    result = await asyncio.to_thread(control, 'poll', {})
                    envelope = result.get('request')
                    if envelope is None:
                        await asyncio.sleep(.25 if active else 2)
                        continue
                    active.add(asyncio.create_task(handle(envelope)))
            finally:
                # Revocation, signal or transport failure cancels every owned task.
                # Each executor terminates its own retained Popen group; no replay.
                for task in active:
                    task.cancel()
                await asyncio.gather(*active, return_exceptions=True)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--gateway-backend', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--agent-management-grant-id')
    parser.add_argument('--gateway-pid', type=int)
    args = parser.parse_args()
    from worker_signal_lifecycle import run_until_signalled
    raise SystemExit(asyncio.run(run_until_signalled(serve, args)))


if __name__ == '__main__':
    main()
