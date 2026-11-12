"""New MCP boundary tests. Original tools/authority are disclosed test doubles.

These exercise the real MCP SDK list/call protocol; they do not prove original
Holder integration, deployed OAuth identity, native execution or GPT discovery.
"""

import json
import unittest
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from batch_mcp_entry import Item, OriginalBatchScope, mount_batch_tools


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.resolutions = []
        self.current = True
        self.denied = False
        self.unknown = False
        self.revoke_during_call = False
        self.server = FastMCP("existing-server-test-double")

        async def submit(**kwargs):
            self.calls.append(("submit", kwargs))
            if self.unknown:
                raise RuntimeError("PRIVATE_TEST_SENTINEL_MUST_NOT_LEAK")
            if self.revoke_during_call:
                self.current = False
            return SimpleNamespace(update={"messages": [SimpleNamespace(
                name="batch_task", status="success", content="Recorded pending; automatic_dispatch=False",
            )]})

        async def status(**kwargs):
            self.calls.append(("read", kwargs))
            return '{"status":"pending","automatic_dispatch":false}'

        async def cancel(**kwargs):
            self.calls.append(("cancel", kwargs))
            return "Cancellation requested; native stop not verified."

        self.tools = tuple(SimpleNamespace(name=name, coroutine=fn) for name, fn in (
            ("batch_task", submit), ("batch_status", status), ("cancel_batch", cancel),
        ))
        self.runtime = object()

        async def resolve(ctx, parent, key, action):
            self.resolutions.append((parent, key, action))
            if self.denied:
                raise RuntimeError("PRIVATE_AUTH_SENTINEL_MUST_NOT_LEAK")
            return OriginalBatchScope(self.runtime, self.tools, Item, "original-stable-call-id", lambda: self.current)

        self.resolve = resolve
        await mount_batch_tools(self.server, resolve_scope=resolve)

    async def call(self, name, request):
        async with create_connected_server_and_client_session(self.server) as session:
            await session.initialize()
            return await session.call_tool(name, {"request": request})

    def submission(self):
        return {"parent_id": "parent", "operation_key": "original-op", "title": "Test",
                "items": [{"key": "a", "prompt": "Read the permitted artifact"}], "subagent_type": "general-purpose"}

    async def test_discovery_and_annotations(self):
        async with create_connected_server_and_client_session(self.server) as session:
            await session.initialize()
            listed = {tool.name: tool for tool in (await session.list_tools()).tools}
        self.assertEqual(set(listed), {"batch_task", "batch_status", "cancel_batch"})
        self.assertTrue(listed["batch_status"].annotations.readOnlyHint)
        self.assertFalse(listed["batch_task"].annotations.readOnlyHint)
        self.assertTrue(listed["cancel_batch"].annotations.destructiveHint)
        self.assertEqual(self.calls, [])

    async def test_submit_forwards_original_runtime_and_stable_identity(self):
        result = await self.call("batch_task", self.submission())
        self.assertFalse(result.isError)
        self.assertEqual(len(self.calls), 1)
        args = self.calls[0][1]
        self.assertIs(args["runtime"], self.runtime)
        self.assertEqual(args["tool_call_id"], "original-stable-call-id")
        self.assertIsInstance(args["items"][0], Item)
        self.assertNotIn("parent_id", args)
        self.assertNotIn("operation_key", args)
        self.assertEqual(self.resolutions, [("parent", "original-op", "submit")])
        self.assertIn("automatic_dispatch=False", json.dumps(result.model_dump()))

    async def test_read_and_cancel_use_separate_original_actions(self):
        request = {"parent_id": "parent", "operation_key": "original-op", "batch_id": "saved-batch"}
        self.assertFalse((await self.call("batch_status", request)).isError)
        self.assertFalse((await self.call("cancel_batch", request)).isError)
        self.assertEqual([call[0] for call in self.calls], ["read", "cancel"])
        self.assertEqual([call[2] for call in self.resolutions], ["read", "cancel"])

    async def test_authority_denied_has_no_underlying_call_or_private_error(self):
        self.denied = True
        result = await self.call("batch_task", self.submission())
        self.assertTrue(result.isError)
        self.assertEqual(self.calls, [])
        self.assertIn("BATCH_SUBMISSION_DENIED", str(result))
        self.assertNotIn("PRIVATE_AUTH", str(result))

    async def test_revoked_scope_has_no_underlying_call(self):
        self.current = False
        result = await self.call("batch_task", self.submission())
        self.assertTrue(result.isError)
        self.assertEqual(self.calls, [])

    async def test_model_cannot_supply_owner_or_runtime(self):
        for field in ("user_id", "runtime", "tool_call_id", "provider"):
            request = self.submission()
            request[field] = "forged"
            result = await self.call("batch_task", request)
            self.assertTrue(result.isError)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.resolutions, [])

    async def test_invalid_or_duplicate_items_do_not_resolve_or_submit(self):
        for items in ([], [{"key": "a", "prompt": " "}], [{"key": "a", "prompt": "x"}] * 2):
            request = self.submission()
            request["items"] = items
            self.assertTrue((await self.call("batch_task", request)).isError)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.resolutions, [])

    async def test_unknown_result_is_not_retried_or_relabelled_success(self):
        self.unknown = True
        result = await self.call("batch_task", self.submission())
        self.assertTrue(result.isError)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("BATCH_CALL_OUTCOME_UNKNOWN_QUERY_ORIGINAL_KEY", str(result))
        self.assertNotIn("PRIVATE_TEST", str(result))

    async def test_duplicate_mount_does_not_replace_existing_tools(self):
        with self.assertRaisesRegex(ValueError, "ALREADY_REGISTERED"):
            await mount_batch_tools(self.server, resolve_scope=self.resolve)
        self.assertEqual(len(await self.server.list_tools()), 3)

    async def test_late_revocation_suppresses_success_without_repeating_effect(self):
        self.revoke_during_call = True
        result = await self.call("batch_task", self.submission())
        self.assertTrue(result.isError)
        self.assertEqual(len(self.calls), 1)
        self.assertIn("UNKNOWN_QUERY_ORIGINAL_KEY", str(result))
        self.assertNotIn("Recorded pending", str(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
