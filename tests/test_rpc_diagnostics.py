import asyncio
import hashlib
import os
import unittest
from unittest.mock import patch,AsyncMock
from rpc_diagnostics import measure_await,install_rpc_diagnostic
from test_mcp_installer import FakeMCP


class Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config=dict(diagnostic_enabled=True,budgets_verified=True,max_wait_seconds=10,function_budget_seconds=30,connector_budget_seconds=30)
        self.env=patch.dict(os.environ,{'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':'ab'*32});self.env.start();self.addCleanup(self.env.stop)

    async def test_bounded_measurement_no_completion_claim(self):
        result=await measure_await(.01,principal=lambda:'owner',host_config=self.config)
        self.assertGreaterEqual(result['elapsed_seconds'],.01)
        self.assertTrue(result['configured'])
        self.assertEqual(result['host_public_key_sha256'],hashlib.sha256(bytes.fromhex('ab'*32)).hexdigest())
        for field in ('source_accessed','event_sent','state_written','host_reply_verified','end_to_end_performance_verified'):
            self.assertFalse(result[field])
        self.assertNotIn('ab'*32,str(result))

    async def test_missing_configuration_still_safe_diagnostic(self):
        with patch.dict(os.environ,{'XJ_SOURCE_RPC_HOST_PUBLIC_KEY':''}):
            result=await measure_await(.001,principal=lambda:'owner',host_config={'diagnostic_enabled':True})
            self.assertFalse(result['configured']);self.assertIsNone(result['host_public_key_sha256'])

    async def test_invalid_duration_rejected(self):
        for value in (0,-1,6,float('nan'),float('inf'),True):
            with self.subTest(value=value),self.assertRaises(ValueError):
                await measure_await(value,principal=lambda:'owner',host_config=self.config)

    async def test_explicit_disabled_prevents_wait_and_tool(self):
        for config in ({},{'diagnostic_enabled':False}):
            with self.assertRaisesRegex(ValueError,'DISABLED'):
                await measure_await(.001,principal=lambda:'owner',host_config=config)
            mcp=FakeMCP()
            result=install_rpc_diagnostic(mcp,lambda:'owner',{},host_config=config)
            self.assertFalse(result['installed']);self.assertEqual(mcp.tools,{})

    async def test_host_configured_45_second_probe_mock_only(self):
        config=dict(self.config,diagnostic_max_wait_seconds=60,budgets_verified=False)
        # No real 45-second wait or live probe: verify forwarding with mocked time.
        with patch('rpc_diagnostics.asyncio.sleep',new_callable=AsyncMock) as sleep, \
                patch('rpc_diagnostics.time.monotonic',side_effect=[100,145.02]):
            result=await measure_await(45,principal=lambda:'owner',host_config=config)
            sleep.assert_awaited_once_with(45)
        self.assertEqual(result['observed_minimum_supported_seconds'],45)
        self.assertFalse(result['maximum_timeout_verified'])
        self.assertFalse(result['configured'])
        self.assertFalse(config['budgets_verified'])

    async def test_above_one_eighty_and_invalid_host_limits(self):
        config=dict(self.config,diagnostic_max_wait_seconds=180)
        with self.assertRaises(ValueError):await measure_await(181,principal=lambda:'owner',host_config=config)
        for limit in (0,-1,181,True,float('inf')):
            config=dict(self.config,diagnostic_max_wait_seconds=limit)
            with self.assertRaises(ValueError):await measure_await(.001,principal=lambda:'owner',host_config=config)

    async def test_host_configured_120_second_probe_mock_only(self):
        config=dict(self.config,diagnostic_max_wait_seconds=180,budgets_verified=False)
        with patch('rpc_diagnostics.asyncio.sleep',new_callable=AsyncMock) as sleep, \
                patch('rpc_diagnostics.time.monotonic',side_effect=[100,220.02]):
            result=await measure_await(120,principal=lambda:'owner',host_config=config)
            sleep.assert_awaited_once_with(120)
        self.assertEqual(result['observed_minimum_supported_seconds'],120)
        self.assertFalse(result['maximum_timeout_verified'])
        self.assertFalse(result['configured'])
        for field in ('source_accessed','event_sent','state_written','host_reply_verified','end_to_end_performance_verified'):
            self.assertFalse(result[field])

    async def test_missing_owner_denied(self):
        for owner in ('',None):
            with self.assertRaisesRegex(ValueError,'OWNER_REQUIRED'):
                await measure_await(.001,principal=lambda:owner,host_config=self.config)

    async def test_cancel_does_not_claim_success(self):
        task=asyncio.create_task(measure_await(1,principal=lambda:'owner',host_config=self.config))
        await asyncio.sleep(0);task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task

    async def test_owner_changed_rejected(self):
        owners=iter(['owner','other'])
        with self.assertRaisesRegex(ValueError,'OWNER_CHANGED'):
            await measure_await(.001,principal=lambda:next(owners),host_config=self.config)

    def test_optional_same_mcp_read_only(self):
        mcp=FakeMCP();mcp.tools['existing']='preserved'
        install_rpc_diagnostic(mcp,lambda:'owner',{},host_config=self.config)
        self.assertEqual(mcp.tools['existing'],'preserved')
        self.assertTrue(mcp.metadata['gateway_source_rpc_diagnostic']['annotations'].readOnlyHint)

if __name__=='__main__':unittest.main()
