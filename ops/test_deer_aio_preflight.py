"""Configuration-input tests only; no container-isolation claim."""
import copy
import importlib.util
from pathlib import Path
import unittest

from deer_aio_preflight import FILES, render_candidate


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.image = 'example.test/aio@sha256:' + 'a' * 64
        self.proxy = 'example.test/proxy@sha256:' + 'b' * 64
        self.report = dict(schema='deer-aio-host-preflight-v1',
            status='PRECHECK_COLLECTED_NOT_ADMITTED', docker_server={'exit_code': 0},
            source_files={name: {'present': True} for name in FILES},
            agent_sandbox_sdk_present=True,
            cached_images=[dict(repository='example.test/aio', digest='sha256:'+'a'*64),
                           dict(repository='example.test/proxy', digest='sha256:'+'b'*64)])

    def test_missing_runtime_dependency_cannot_render_candidate(self):
        for change in ('source', 'sdk', 'daemon'):
            report = copy.deepcopy(self.report)
            if change == 'source':
                report['source_files'].pop(FILES[0])
            elif change == 'sdk':
                report['agent_sandbox_sdk_present'] = False
            else:
                report['docker_server']['exit_code'] = 1
            with self.assertRaisesRegex(ValueError, 'PREFLIGHT_INCOMPLETE'):
                render_candidate(report, image=self.image, proxy_image=self.proxy)

    def test_floating_or_uncached_image_never_causes_a_pull(self):
        with self.assertRaisesRegex(ValueError, 'DIGESTS_REQUIRED'):
            render_candidate(self.report, image='example.test/aio:latest', proxy_image=self.proxy)
        with self.assertRaisesRegex(ValueError, 'NO_AUTOMATIC_PULL'):
            render_candidate(self.report, image=self.image.replace('a'*64, 'c'*64), proxy_image=self.proxy)

    def test_candidate_matches_the_original_deer_configuration_schema(self):
        backend = Path(__file__).resolve().parents[2] / 'entry_check/unpacked/Gateway_Shared_Batch_Entry_20261105/author/source/backend'
        source = backend / 'packages/harness/deerflow/config/sandbox_config.py'
        if not source.is_file():
            self.skipTest('original configuration schema not available')
        spec = importlib.util.spec_from_file_location('original_sandbox_config', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        candidate = render_candidate(self.report, image=self.image, proxy_image=self.proxy)
        config = module.SandboxConfig.model_validate(candidate['sandbox_fragment'])
        self.assertEqual(config.network.mode, 'isolated')
        self.assertFalse(config.allow_host_bash)
        self.assertEqual(config.mounts, [])
        self.assertEqual(candidate['status'], 'CANDIDATE_NOT_APPLIED_NOT_LAUNCH_AUTHORITY')


if __name__ == '__main__':
    unittest.main()
