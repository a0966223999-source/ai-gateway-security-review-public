"""Test narrowing only; no live grant or account approval is made here."""

import unittest

from deer_agent_management import AgentManagementDenied, DeerAgentManagement, OriginalAgentBinding
from deer_management_test_scope import TEST_AGENT_NAME, narrow_test_binding


class TestScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now, self.calls = 1000, []
        async def authorize(context, action, payload):
            return True
        async def invoke(method, path, body):
            self.calls.append((method, path))
            return {"agents": [{"name": TEST_AGENT_NAME}, {"name": "unrelated-agent"}]}
        self.original = OriginalAgentBinding("fixture-owner", "fixture-user", authorize,
            invoke, authority_scope="original-deer-agent-management")
        self.binding = narrow_test_binding(self.original, expires_at=1600, clock=lambda: self.now)
        self.service = DeerAgentManagement(resolve_binding=lambda ctx: self.binding,
                                          principal=lambda: "fixture-owner")

    async def test_list_only_returns_named_test_role(self):
        self.assertEqual(await self.service.list_agents(None), {"agents": [{"name": TEST_AGENT_NAME}]})

    async def test_other_agent_name_never_reaches_original_transport(self):
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
            await self.service.read_agent(None, "other-agent")
        self.assertEqual(self.calls, [])

    async def test_expired_window_never_reaches_original_transport(self):
        self.now = 1600
        with self.assertRaisesRegex(AgentManagementDenied, "WINDOW_EXPIRED"):
            await self.service.list_agents(None)
        self.assertEqual(self.calls, [])

    async def test_checkpoint_scope_cannot_be_upgraded(self):
        from dataclasses import replace
        with self.assertRaisesRegex(AgentManagementDenied, "EXISTING_MANAGEMENT_GRANT_REQUIRED"):
            narrow_test_binding(replace(self.original, authority_scope="platform-checkpoint-only"),
                                expires_at=1600, clock=lambda: self.now)

    async def test_nonempty_tool_or_delegation_lists_are_rejected(self):
        for field in ("tool_groups", "skills", "allowed_subagents"):
            with self.subTest(field=field):
                request = {"name": TEST_AGENT_NAME, "soul": "Read only", "operation_key": "test", field: ["anything"]}
                with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
                    await self.service.create_agent(None, request)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
