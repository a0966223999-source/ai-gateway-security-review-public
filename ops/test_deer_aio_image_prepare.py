"""Image-selection regression tests; no runtime or isolation claims."""
import unittest

from deer_aio_image_prepare import select_pinned_image


class ImageSelectionTests(unittest.TestCase):
    def test_selects_machine_read_descriptor_without_digest_transcription(self):
        descriptor = {'platform': {'architecture': 'amd64', 'os': 'linux'},
                      'digest': 'sha256:' + 'a' * 64}
        self.assertEqual(select_pinned_image({'manifests': [descriptor]}),
                         'ghcr.io/agent-infra/sandbox@sha256:' + 'a' * 64)

    def test_rejects_missing_ambiguous_and_malformed_descriptor(self):
        descriptor = {'platform': {'architecture': 'amd64', 'os': 'linux'},
                      'digest': 'sha256:' + 'a' * 64}
        for manifest in ({}, {'manifests': [descriptor, descriptor]},
                         {'manifests': [dict(descriptor, digest='latest')]}):
            with self.assertRaises(ValueError):
                select_pinned_image(manifest)


if __name__ == '__main__':
    unittest.main()
