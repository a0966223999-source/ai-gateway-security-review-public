"""Original HTTP/OAuth middleware; batch authority/tools are explicit fixtures."""
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

import mcp_bridge
from batch_mcp_entry import Item, OriginalBatchScope


class Verifier:
    issuer = 'https://fixture.auth0.com/'

    async def verify_token(self, token):
        if token != 'fixture-token':
            return None
        return AccessToken(token=token, client_id='fixture-client', scopes=['synthetic:events'],
                           expires_at=int(time.time()) + 1000, subject='fixture-owner')


class OriginalMountTests(unittest.TestCase):
    def test_original_authenticated_server_advertises_and_calls_bound_batch_tool(self):
        calls = []
        resolutions = []

        async def original_status(**kwargs):
            calls.append(kwargs)
            return '{"status":"pending","automatic_dispatch":false}'

        tools = tuple(SimpleNamespace(name=name, coroutine=original_status) for name in
                      ('batch_task', 'batch_status', 'cancel_batch'))

        async def resolve(ctx, parent, operation, action):
            token = get_access_token()
            resolutions.append((token.subject, parent, operation, action))
            if parent != 'authorized-parent':
                raise PermissionError('fixture-only-denial')
            return OriginalBatchScope(object(), tools, Item, 'original-call-id', lambda: True)

        env = dict(VERCEL_URL='fixture.vercel.app', XJ_MCP_PUBLIC_HOST='fixture.vercel.app',
                   VERCEL_ENV='preview', XJ_AUTH0_ALLOWED_SUBJECT='fixture-owner',
                   XJ_INBOX_SECRET_KEY='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
        with patch.dict(os.environ, env, clear=True), patch.object(mcp_bridge, 'Auth0Verifier', Verifier):
            app = FastAPI()
            mcp_bridge.install(app, lambda *a: None, lambda *a: None, dict,
                               batch_scope_resolver=resolve)
            with TestClient(app, base_url='https://fixture.vercel.app') as client:
                def call(method, params, token='fixture-token'):
                    headers = {'Accept': 'application/json, text/event-stream'}
                    if token:
                        headers['Authorization'] = 'Bearer ' + token
                    return client.post('/mcp', headers=headers,
                                       json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
                self.assertEqual(call('tools/list', {}, token='').status_code, 401)
                self.assertEqual(call('tools/list', {}, token='wrong').status_code, 401)
                records = {t['name']: t for t in call('tools/list', {}).json()['result']['tools']}
                self.assertIn('redis_test_read', records)
                self.assertIn('gateway_inbox_send', records)
                for name in ('batch_task', 'batch_status', 'cancel_batch'):
                    self.assertIn(name, records)
                    self.assertEqual(records[name]['_meta']['securitySchemes'][0]['type'], 'oauth2')
                request = {'parent_id': 'authorized-parent', 'operation_key': 'original-key', 'batch_id': 'saved-batch'}
                result = call('tools/call', {'name': 'batch_status', 'arguments': {'request': request}}).json()['result']
                self.assertFalse(result.get('isError', False))
                self.assertEqual(len(calls), 1)
                self.assertEqual(resolutions, [('fixture-owner', 'authorized-parent', 'original-key', 'read')])
                request['parent_id'] = 'wrong-parent'
                denied = call('tools/call', {'name': 'batch_status', 'arguments': {'request': request}}).json()['result']
                self.assertTrue(denied['isError'])
                self.assertEqual(len(calls), 1)

    def test_invalid_startup_resolver_rejected(self):
        with self.assertRaisesRegex(ValueError, 'BATCH_SCOPE_RESOLVER_REQUIRED'):
            mcp_bridge.install(FastAPI(), None, None, None, batch_scope_resolver='not-a-capability')


if __name__ == '__main__':
    unittest.main()
