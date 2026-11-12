"""Real original auth/CSRF/Source routes; identity and registry reads are fixtures."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch, AsyncMock
import test_gateway_agent_consent as management_fixture
from gateway_runtime_consent import install_gateway_runtime_consent


class RuntimeHttpTests(unittest.TestCase):
    def setUp(self):
        management_fixture.ConsentHttpTests.setUp(self)
        install_gateway_runtime_consent(self.app,enabled=True)
        self.base='/api/threads/fixture-thread/agent-runtime-consents'
        self.body=dict(operation_key='runtime-consent',name='runtime-fixture',ttl_seconds=600,
            confirmation='existing-codex-cli-read-only-two-runs-no-delegation')
        self.snapshot=patch('deer_fixed_runtime_host.definition_snapshot',new=AsyncMock(
            return_value=({},'c'*64)))
        self.store=patch('deerflow.persistence.agents.get_agent_store',return_value=SimpleNamespace())
        for p in (self.snapshot,self.store):p.start();self.addCleanup(p.stop)

    def create(self,**changes):
        return self.client.post(self.base,headers=self.headers,json={**self.body,**changes})

    def test_exact_runtime_scope_independent_query_and_revoke(self):
        result=self.create();self.assertEqual(result.status_code,200,result.text)
        record=result.json()
        self.assertTrue(record['model_execution']);self.assertEqual(record['max_runs'],2)
        self.assertEqual(record['definition_sha256'],'c'*64)
        self.assertEqual(self.client.get(self.base+'/by-operation/runtime-consent',headers=self.headers).json(),record)
        self.assertEqual(self.create().status_code,409)
        self.assertTrue(self.client.delete(self.base+'/'+record['grant_id'],headers=self.headers).json()['revoked'])

    def test_management_confirmation_or_caller_hash_cannot_issue_runtime_grant(self):
        self.assertEqual(self.create(confirmation='named-definitions-only-no-models-tools-or-delegation').status_code,422)
        self.assertEqual(self.create(definition_sha256='b'*64).status_code,422)
        self.assertEqual(self.create(ttl_seconds=601).status_code,422)

    def test_session_csrf_thread_and_policy_gates_retained(self):
        for headers in ({'x-fixture-user':'fixture-owner'},
                        {**self.headers,'x-fixture-origin':'api_key'},
                        {**self.headers,'x-fixture-role':'user'}):
            self.assertIn(self.client.post(self.base,headers=headers,json=self.body).status_code,(401,403))
        self.metadata={'user_id':'other'}
        self.assertEqual(self.create().status_code,403)

    def test_review_page_no_automatic_grant_and_default_off(self):
        from fastapi import FastAPI
        app=FastAPI()
        self.assertFalse(install_gateway_runtime_consent(app))
        self.assertFalse(any('agent-runtime' in r.path for r in app.routes))
        page=self.client.get('/api/v1/auth/agent-runtime-consent')
        self.assertEqual(page.status_code,200)
        self.assertIn('qa-dr-runtime-20261113',page.text)
        self.assertIn('90 秒',page.text)
        self.assertIn('消耗原账户额度',page.text)
        self.assertEqual(list(self.runtime.root.glob('agent-runtime-consent-*')),[])
