"""Single new pending batch on the existing repository, followed by cancellation.

This verifies the fresh host's real original-tool composition, not OAuth/GPT,
native dispatch, Work identity or self-wake. It never runs a model or child.
"""
import argparse
import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time

from fresh_batch_host import FreshBatchHost, HostDenied, canonical, digest, exclusive_write


async def probe(args):
    backend = Path(args.gateway_backend).resolve(strict=True)
    database = Path(args.database).resolve(strict=True)
    sys.path[:0] = [str(backend), str(backend / 'packages/harness'), str(backend / 'packages/extension-api')]
    os.chdir(backend)
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from deerflow.persistence.subagent_batches.sql import SubagentBatchRepository
    host = FreshBatchHost(args.root)
    operation = 'fresh-core-' + secrets.token_hex(12)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(database))
    repository = SubagentBatchRepository(async_sessionmaker(engine, expire_on_commit=False))

    def envelope(action, key, payload):
        c = host.config
        body = dict(generation=c['generation'], owner=c['owner'], parent_id=c['parent_id'],
                    action=action, operation_key=key, payload=payload,
                    request_id=digest([c['generation'], c['owner'], action, key]),
                    expires_at=min(time.time() + 60, c['expires_at']))
        return dict(body=body, mac=hmac.new(host.ingress, canonical(body), 'sha256').hexdigest())

    def stored_rows():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return db.execute('SELECT id FROM subagent_batches WHERE user_id=? AND submission_key=?',
                              (host.config['owner'], host.config['generation'] + ':' + operation)).fetchall()
    try:
        assert stored_rows() == []
        request = envelope('submit', operation, dict(title='Fresh host bounded integration probe',
            subagent_type='general-purpose', items=[dict(key='one', prompt='Synthetic read-only integration item. Do not dispatch.')],
            max_live_items=1, max_running_items=1))
        submitted = await host.execute(request, repository=repository)
        rows = stored_rows()
        assert len(rows) == 1, 'ORIGINAL_BATCH_NOT_RECORDED'
        batch_id = rows[0][0]
        status = await host.execute(envelope('read', operation + '-read', dict(batch_id=batch_id)), repository=repository)
        restarted = FreshBatchHost(args.root)
        try:
            restarted.reserve(request['body'])
            raise AssertionError('RESTART_ALLOWED_REPLAY')
        except HostDenied as error:
            assert str(error) == 'ATTEMPTED_QUERY_ONLY'
        cancelled = await host.execute(envelope('cancel', operation + '-cancel', dict(batch_id=batch_id)), repository=repository)
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            final = db.execute('SELECT status,attempt FROM subagent_batch_items WHERE batch_id=?', (batch_id,)).fetchall()
        assert final == [('cancelled', 0)], 'UNSTARTED_ITEM_NOT_CANCELLED'
        result = dict(generation=host.config['generation'], parent_id=host.config['parent_id'], operation_key=operation,
                      batch_id=batch_id, rows=len(stored_rows()), final_items=final,
                      submit=submitted['body']['result'], status=status['body']['result'],
                      cancel=cancelled['body']['result'], restart_fence='PASS',
                      automatic_dispatch=False, native_execution_verified=False, oauth_tested=False)
        exclusive_write(host.root / ('probe-' + operation + '.json'), canonical(result))
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--gateway-backend', required=True)
    parser.add_argument('--database', required=True)
    asyncio.run(probe(parser.parse_args()))
