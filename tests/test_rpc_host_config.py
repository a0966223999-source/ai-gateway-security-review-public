import os
import unittest
from unittest.mock import patch
from rpc_host_config import host_rpc_config


class ConfigTests(unittest.TestCase):
    def test_defaults_disabled(self):
        with patch.dict(os.environ, {}, clear=True):
            c = host_rpc_config()
        self.assertFalse(c['budgets_verified'])
        self.assertFalse(c['diagnostic_enabled'])
        self.assertIsNone(c['function_budget_seconds'])
        self.assertIsNone(c['connector_budget_seconds'])
        self.assertEqual(c['max_wait_seconds'], 30)
        self.assertEqual(c['diagnostic_max_wait_seconds'], 5)

    def test_explicit_settings_only(self):
        with patch.dict(os.environ, {
            'XJ_SOURCE_RPC_BUDGETS_VERIFIED': 'true',
            'XJ_SOURCE_RPC_DIAGNOSTIC_ENABLED': 'true',
            'XJ_SOURCE_RPC_FUNCTION_BUDGET_SECONDS': '60',
            'XJ_SOURCE_RPC_CONNECTOR_BUDGET_SECONDS': '45',
            'XJ_SOURCE_RPC_MAX_WAIT_SECONDS': '30',
            'XJ_SOURCE_RPC_DIAGNOSTIC_MAX_WAIT_SECONDS': '60',
        }, clear=True):
            c = host_rpc_config()
        self.assertTrue(c['budgets_verified'])
        self.assertTrue(c['diagnostic_enabled'])
        self.assertEqual(c['function_budget_seconds'], 60)
        self.assertEqual(c['connector_budget_seconds'], 45)

    def test_invalid_numbers_are_unavailable(self):
        for bad in ('NaN', 'inf', '-1', '0', 'not-a-number', ''):
            with self.subTest(bad=bad), patch.dict(os.environ, {
                'XJ_SOURCE_RPC_FUNCTION_BUDGET_SECONDS': bad,
                'XJ_SOURCE_RPC_DIAGNOSTIC_MAX_WAIT_SECONDS': bad,
                'XJ_SOURCE_RPC_BUDGETS_VERIFIED': 'TRUE',
            }, clear=True):
                c = host_rpc_config()
                self.assertIsNone(c['function_budget_seconds'])
                self.assertIsNone(c['diagnostic_max_wait_seconds'])
                self.assertFalse(c['budgets_verified'])


if __name__ == '__main__':
    unittest.main()
