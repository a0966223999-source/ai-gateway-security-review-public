"""Run fresh batch host against an EXISTING original Gateway SQLite database."""
import argparse
import asyncio
import os
from pathlib import Path
import sqlite3
import sys


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
    from fresh_batch_host import FreshBatchHost, private_read
    from fresh_batch_rpc import run_worker
    from service_extension import original_redis_connection
    host = FreshBatchHost(args.root)
    # Secret delivery is separate from code/config. Never accept request URLs.
    os.environ['XJ_PREVIEW_REDIS_URL'] = private_read(host.root / 'redis.url').decode()
    os.environ['VERCEL_ENV'] = 'preview'
    engine = create_async_engine('sqlite+aiosqlite:///' + str(database), connect_args={'timeout': 5})
    repository = SubagentBatchRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        with original_redis_connection() as redis:
            await run_worker(host, redis, repository)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--gateway-backend', required=True)
    parser.add_argument('--database', required=True)
    asyncio.run(serve(parser.parse_args()))


if __name__ == '__main__':
    main()
