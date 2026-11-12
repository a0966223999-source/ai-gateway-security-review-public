import json
import os
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.provider import AccessToken
import mcp_bridge


class Verifier:
    issuer = 'https://fixture.auth0.com/'
    async def verify_token(self, token):
        if token != 'fixture-token':
            return None
        return AccessToken(token=token, client_id='fixture-client', scopes=['synthetic:events'],
                           expires_at=int(time.time()) + 1000, subject='fixture-owner')


class MountTests(unittest.TestCase):
    def run_client(self, extra, check):
        env = dict(VERCEL_URL='fixture.vercel.app', XJ_MCP_PUBLIC_HOST='fixture.vercel.app',
                   VERCEL_ENV='preview', XJ_AUTH0_ALLOWED_SUBJECT='fixture-owner',
                   XJ_INBOX_SECRET_KEY='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=')
        env.update(extra)
        with patch.dict(os.environ, env, clear=True), patch.object(mcp_bridge, 'Auth0Verifier', Verifier):
            app = FastAPI()
            mcp_bridge.install(app, lambda *a: None, lambda *a: None, dict)
            with TestClient(app, base_url='https://fixture.vercel.app') as client:
                def call(method, params):
                    response = client.post('/mcp', headers={'Authorization':'Bearer fixture-token',
                        'Accept':'application/json, text/event-stream'},
                        json={'jsonrpc':'2.0','id':1,'method':method,'params':params})
                    self.assertEqual(response.status_code, 200)
                    return response.json()
                check(call)

    def test_unconfigured_new_tools_absent_original_preserved(self):
        def check(call):
            names = {x['name'] for x in call('tools/list', {})['result']['tools']}
            self.assertIn('gateway_inbox_send', names)
            self.assertIn('gateway_inbox_subscription_status', names)
            self.assertFalse(any(x.startswith('gateway_source_rpc') for x in names))
            events = {x['name'] for x in call('events/list', {})['result']['events']}
            self.assertEqual(events, {'inbox.message.created','gateway.continuation.ready'})
        self.run_client({}, check)

    def test_diagnostic_only_has_no_source_rpc(self):
        def check(call):
            names = {x['name'] for x in call('tools/list', {})['result']['tools']}
            self.assertIn('gateway_source_rpc_diagnostic', names)
            self.assertNotIn('gateway_source_rpc_request', names)
            result = call('tools/call', {'name':'gateway_source_rpc_diagnostic','arguments':{'wait_seconds':0.001}})['result']
            self.assertFalse(result.get('isError', False))
            value = result.get('structuredContent') or json.loads(result['content'][0]['text'])
            self.assertFalse(value['source_accessed'])
            self.assertFalse(value['event_sent'])
            self.assertFalse(value['state_written'])
            self.assertFalse(value['configured'])
        self.run_client({'XJ_SOURCE_RPC_DIAGNOSTIC_ENABLED':'true'}, check)

    def test_explicit_rpc_same_mcp_registered_with_annotations(self):
        def check(call):
            records = {x['name']:x for x in call('tools/list', {})['result']['tools']}
            for name in ['request','read','respond','query']:
                self.assertIn('gateway_source_rpc_'+name, records)
            self.assertTrue(records['gateway_source_rpc_read']['annotations']['readOnlyHint'])
            self.assertTrue(records['gateway_source_rpc_query']['annotations']['readOnlyHint'])
            self.assertFalse(records['gateway_source_rpc_request']['annotations']['readOnlyHint'])
            result = call('tools/call', {'name':'gateway_source_rpc_query', 'arguments':{
                'mailbox_id':'unapproved-mailbox','request_id':'a'*64}})['result']
            self.assertTrue(result['isError'])
        self.run_client({'XJ_SOURCE_RPC_BUDGETS_VERIFIED':'true',
            'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':'11'*32,'XJ_SOURCE_RPC_MAX_WAIT_SECONDS':'30',
            'XJ_SOURCE_RPC_FUNCTION_BUDGET_SECONDS':'60','XJ_SOURCE_RPC_CONNECTOR_BUDGET_SECONDS':'45'}, check)


if __name__ == '__main__':
    unittest.main()
