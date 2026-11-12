"""Minimal patches preserve existing Gateway hooks and refuse changed anchors."""
import ast
import unittest
from install_fixed_runtime import gateway_hooks


class RuntimeInstallGuardsTests(unittest.TestCase):
    def setUp(self):
        self.app='''def configure():
    install_gateway_management_consent(app, enabled=management_environment.get("XJ_AGENT_MANAGEMENT_CONSENT_ENABLED") == "true")
    install_checkpoint_host_route(app)
    preserve_short_memory()
'''
        self.auth='''PUBLIC = {
        "/api/v1/auth/agent-management-consent",  # Static review page only.
}
'''

    def test_only_runtime_hook_and_static_review_page_added(self):
        app,auth=gateway_hooks(self.app,self.auth)
        ast.parse(app);ast.parse(auth)
        self.assertIn(b'preserve_short_memory()',app)
        self.assertIn(b'install_checkpoint_host_route(app)',app)
        self.assertIn(b'XJ_FIXED_AGENT_RUNTIME_CONSENT_ENABLED',app)
        self.assertNotIn(b'/agent-runtime-consents',auth)
        self.assertIn(b'/api/v1/auth/agent-runtime-consent',auth)

    def test_duplicate_or_changed_anchor_is_refused(self):
        for app,auth in ((self.app+self.app,self.auth),(self.app,self.auth+self.auth),
                         (self.app.replace('XJ_AGENT_MANAGEMENT','OTHER'),self.auth)):
            with self.assertRaisesRegex(RuntimeError,'HOOK_CHANGED'):
                gateway_hooks(app,auth)

    def test_second_install_never_duplicates_hook(self):
        app,auth=gateway_hooks(self.app,self.auth)
        with self.assertRaisesRegex(RuntimeError,'HOOK_CHANGED'):
            gateway_hooks(app.decode(),auth.decode())
