"""Offline allowlist tests; never inspect the actual process environment."""
import unittest

from collect_work_self_report import collect_runtime_identifiers


class GuardedEnvironment(dict):
    def get(self, key, default=None):
        if key not in {'CODEX_THREAD_ID', 'CODEX_ENVIRONMENT_ID'}:
            raise AssertionError('Unexpected environment access')
        return super().get(key, default)


class WorkSelfReportTests(unittest.TestCase):
    def test_exact_allowlist(self):
        environment = GuardedEnvironment(
            CODEX_THREAD_ID='fixture-thread', CODEX_ENVIRONMENT_ID='fixture-runtime',
            SECRET='must-not-copy', USER='must-not-copy')
        self.assertEqual(collect_runtime_identifiers(environment), {
            'CODEX_THREAD_ID': 'fixture-thread',
            'CODEX_ENVIRONMENT_ID': 'fixture-runtime',
        })

    def test_missing_is_not_invented(self):
        self.assertEqual(collect_runtime_identifiers(GuardedEnvironment()), {})

    def test_does_not_mutate_input(self):
        environment = GuardedEnvironment(CODEX_THREAD_ID='fixture-thread')
        result = collect_runtime_identifiers(environment)
        result['CODEX_THREAD_ID'] = 'changed'
        self.assertEqual(environment['CODEX_THREAD_ID'], 'fixture-thread')


if __name__ == '__main__':
    unittest.main()
