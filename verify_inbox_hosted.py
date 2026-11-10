"""Explicit one-shot Preview build check; no callbacks or formal queue access."""
import json
import os
import secrets
from urllib.parse import urlsplit

from redis import Redis
from inbox import Inbox, InboxError


def main():
    if os.environ.get('VERCEL_ENV') != 'preview':
        raise RuntimeError('PREVIEW_ONLY')
    url = os.getenv('XJ_PREVIEW_REDIS_URL') or os.environ['REDIS_URL']
    parsed = urlsplit(url)
    if parsed.scheme != 'rediss' or not parsed.hostname or parsed.query or parsed.fragment:
        raise RuntimeError('TLS_CONFIGURATION_REQUIRED')
    with Redis.from_url(url, decode_responses=True, ssl_cert_reqs='required',
                        ssl_check_hostname=True, socket_connect_timeout=3, socket_timeout=3) as client:
        inbox = Inbox('synthetic-hosted-inbox-check', 'verify-' + secrets.token_hex(12), client)
        checks = {}
        try:
            first = inbox.send('one', 'synthetic inbox backend verification')
            checks['write'] = not first['duplicate']
            checks['dedup'] = inbox.send('one', 'synthetic inbox backend verification')['duplicate']
            try:
                inbox.send('one', 'different payload')
            except InboxError as error:
                checks['conflict'] = str(error) == 'operation_conflict'
            checks['list'] = inbox.list()['items'][0]['event_id'] == first['event_id']
            claimed = inbox.claim(first['event_id'])
            checks['claim'] = claimed['version'] == 1
            inbox.settle('ack', first['event_id'], claimed['claim_token'], claimed['version'])
            checks['ack'] = inbox.list()['items'][0]['state'] == 'completed'
            checks['ttl'] = 0 < client.ttl(inbox.key) <= 86400
            if len(checks) != 7 or not all(checks.values()):
                raise RuntimeError('SYNTHETIC_BACKEND_CHECK_FAILED')
        finally:
            client.delete(inbox.key)  # Only this newly generated fixture's exact key.
            checks['cleanup'] = client.exists(inbox.key) == 0
        if not checks['cleanup']:
            raise RuntimeError('SYNTHETIC_CLEANUP_FAILED')
        print(json.dumps({'kind': 'preview_inbox_backend_not_oauth_or_work', 'checks': checks}, sort_keys=True))


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--run-synthetic-hosted-check']:
        raise SystemExit('Explicit --run-synthetic-hosted-check required')
    main()
