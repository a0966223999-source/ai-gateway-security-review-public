"""Isolated original-router/SQLite checks; authority and once-fence are fixtures.

These checks do NOT authenticate a real account or establish a durable fence.
"""

import asyncio
import importlib.util
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine

from app.gateway.routers import agents as original_routes
from deerflow.config.agents_api_config import AgentsApiConfig
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore
from deerflow.persistence.base import Base
from deerflow.runtime.user_context import reset_current_user, set_current_user

from deer_agent_management import (
    AgentManagementDenied, DeerAgentManagement, OriginalAgentBinding,
    install_agent_management_tools,
)


class OriginalRouterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        url = "sqlite:///" + str(Path(self.temp.name) / "original-agents.sqlite")
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[AgentRow.__table__])
        engine.dispose()
        self.store = SqlAgentStore(url)
        self.api_enabled = True
        self.patches = [
            patch.object(original_routes, "get_agent_store", return_value=self.store),
            patch("deerflow.persistence.agents.get_agent_store", return_value=self.store),
            patch.object(original_routes, "get_agents_api_config",
                         side_effect=lambda: AgentsApiConfig(enabled=self.api_enabled)),
        ]
        for item in self.patches:
            item.start()
        app = FastAPI()
        app.include_router(original_routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                       base_url="http://isolated-gateway.test")
        self.calls = []
        self.allowed = True
        self.once_records = {}
        self.owner = "fixture-mcp-owner"
        self.user = "fixture-user-a"

        async def authorize(context, action, payload):
            return self.allowed

        async def invoke(method, path, payload):
            self.calls.append((method, path))
            token = set_current_user(SimpleNamespace(id=self.user))
            try:
                response = await self.client.request(method, path, json=payload)
                response.raise_for_status()
                return response.json()
            finally:
                reset_current_user(token)

        async def fixture_once(action, key, digest, perform):
            # Deliberately memory-only fixture. Not production idempotence proof.
            identity = (self.owner, self.user, action, key)
            if identity in self.once_records:
                old_digest, result = self.once_records[identity]
                if old_digest != digest:
                    raise AgentManagementDenied("FIXTURE_OPERATION_CONFLICT")
                if result is None:
                    raise AgentManagementDenied("FIXTURE_UNKNOWN_QUERY_ONLY")
                return result
            self.once_records[identity] = (digest, None)
            result = await perform()
            self.once_records[identity] = (digest, result)
            return result

        self.binding = OriginalAgentBinding(
            mcp_owner=self.owner, gateway_user_id=self.user,
            authorize=authorize, invoke=invoke, invoke_once=fixture_once,
            authority_scope="original-deer-agent-management")
        self.service = DeerAgentManagement(resolve_binding=lambda ctx: self.binding,
                                          principal=lambda: self.owner)

    async def asyncTearDown(self):
        await self.client.aclose()
        for item in reversed(self.patches):
            item.stop()
        self.store._Session.kw["bind"].dispose()
        self.temp.cleanup()

    def request(self, **updates):
        return {"operation_key": "dr-create-1", "name": "qa-dr-fixed",
                "description": "Isolated receipt reviewer", "soul": "Read only; do not delegate.",
                **updates}

    async def test_original_create_read_and_list_persist_same_owner(self):
        result = await self.service.create_agent(None, self.request())
        self.assertEqual(result["status"], "REGISTRY_SAVED_READBACK_REQUIRED")
        self.assertFalse(result["execution_started"])
        readback = await self.service.read_agent(None, "qa-dr-fixed")
        self.assertEqual(readback["soul"], "Read only; do not delegate.")
        for field in ("tool_groups", "skills", "allowed_subagents"):
            self.assertEqual(readback[field], [])
        listing = await self.service.list_agents(None)
        self.assertEqual([item["name"] for item in listing["agents"]], ["qa-dr-fixed"])
        self.assertTrue(self.store.exists("qa-dr-fixed", user_id=self.user))
        self.assertFalse(self.store.exists("qa-dr-fixed", user_id="fixture-user-b"))

    async def test_original_api_disabled_stays_denied(self):
        self.api_enabled = False
        with self.assertRaises(httpx.HTTPStatusError) as error:
            await self.service.create_agent(None, self.request())
        self.assertEqual(error.exception.response.status_code, 403)
        self.assertFalse(self.store.exists("qa-dr-fixed", user_id=self.user))

    async def test_different_owner_cannot_read_definition(self):
        await self.service.create_agent(None, self.request())
        self.user = "fixture-user-b"
        self.binding = replace(self.binding, gateway_user_id=self.user)
        with self.assertRaises(httpx.HTTPStatusError) as error:
            await self.service.read_agent(None, "qa-dr-fixed")
        self.assertEqual(error.exception.response.status_code, 404)

    async def test_unbound_default_does_not_mount_or_call(self):
        self.assertFalse(install_agent_management_tools(object()))
        with self.assertRaisesRegex(AgentManagementDenied, "NOT_BOUND"):
            await DeerAgentManagement().list_agents(None)
        self.assertEqual(self.calls, [])

    async def test_checkpoint_binding_is_not_management_grant(self):
        self.binding = replace(self.binding, authority_scope="platform-checkpoint-only")
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_REQUIRED"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls, [])

    async def test_truthy_authorization_is_rejected(self):
        self.allowed = 1
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
            await self.service.list_agents(None)
        self.assertEqual(self.calls, [])

    async def test_revocation_before_effect_is_rechecked(self):
        async def revoke_in_fence(action, key, digest, perform):
            self.allowed = False
            return await perform()
        self.binding = replace(self.binding, invoke_once=revoke_in_fence)
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls, [])

    async def test_owner_changed_before_effect_is_rejected(self):
        async def change_owner(action, key, digest, perform):
            self.binding = replace(self.binding, gateway_user_id="fixture-user-b")
            return await perform()
        self.binding = replace(self.binding, invoke_once=change_owner)
        with self.assertRaisesRegex(AgentManagementDenied, "OWNER_CHANGED"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls, [])

    async def test_missing_once_fence_does_not_write(self):
        self.binding = replace(self.binding, invoke_once=None)
        with self.assertRaisesRegex(AgentManagementDenied, "ONCE_FENCE_NOT_BOUND"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls, [])

    async def test_fixture_same_key_does_not_repeat_original_post(self):
        first = await self.service.create_agent(None, self.request())
        second = await self.service.create_agent(None, self.request())
        self.assertEqual(first, second)
        self.assertEqual(self.calls.count(("POST", "/api/agents")), 1)
        with self.assertRaisesRegex(AgentManagementDenied, "OPERATION_CONFLICT"):
            await self.service.create_agent(None, self.request(soul="changed"))

    async def test_name_conflict_is_not_reported_as_our_success(self):
        self.store.create("qa-dr-fixed", {"name": "qa-dr-fixed"}, "preexisting", user_id=self.user)
        with self.assertRaises(httpx.HTTPStatusError) as error:
            await self.service.create_agent(None, self.request())
        self.assertEqual(error.exception.response.status_code, 409)
        self.assertEqual(self.store.get_soul("qa-dr-fixed", user_id=self.user), "preexisting")

    async def test_payload_owner_route_or_inherit_all_injection_is_rejected(self):
        for extra in ({"user_id": "other"}, {"url": "https://other.test"},
                      {"model": "external-model"}, {"allowed_subagents": None}):
            with self.subTest(extra=extra):
                with self.assertRaises(AgentManagementDenied):
                    await self.service.create_agent(None, self.request(**extra))
        self.assertEqual(self.calls, [])

    async def test_path_and_oversize_input_are_rejected(self):
        for name in ("../other", "a/b", "a?x=1", "A"):
            with self.subTest(name=name):
                with self.assertRaises(AgentManagementDenied):
                    await self.service.read_agent(None, name)
        with self.assertRaisesRegex(AgentManagementDenied, "TOO_LARGE"):
            await self.service.create_agent(None, self.request(soul="a" * 65536))
        self.assertEqual(self.calls, [])

    async def test_request_snapshot_survives_caller_mutation(self):
        started, proceed = asyncio.Event(), asyncio.Event()
        original = self.binding.authorize
        async def wait_once(context, action, payload):
            started.set()
            await proceed.wait()
            return await original(context, action, payload)
        self.binding = replace(self.binding, authorize=wait_once)
        request = self.request()
        task = asyncio.create_task(self.service.create_agent(None, request))
        await started.wait()
        request["name"] = "injected-name"
        proceed.set()
        await task
        self.assertTrue(self.store.exists("qa-dr-fixed", user_id=self.user))
        self.assertFalse(self.store.exists("injected-name", user_id=self.user))

    async def test_update_and_stop_do_not_call_unsafe_original_routes(self):
        with self.assertRaisesRegex(AgentManagementDenied, "CAS_NOT_BOUND"):
            await self.service.update_agent(None, {})
        with self.assertRaisesRegex(AgentManagementDenied, "STOP_NOT_BOUND"):
            await self.service.stop_agent(None, "qa-dr-fixed")
        self.assertEqual(self.calls, [])

    async def test_original_mcp_sdk_lists_only_bound_tools_without_context_input(self):
        from mcp.server.fastmcp import FastMCP
        mcp = FastMCP("isolated-dr-agent-management")
        self.assertTrue(install_agent_management_tools(mcp, resolve_binding=lambda ctx: self.binding,
                                                      principal=lambda: self.owner))
        tools = await mcp.list_tools()
        self.assertEqual(sorted(tool.name for tool in tools), ["agent_create", "agent_list", "agent_read", "agent_receipt"])
        for tool in tools:
            self.assertNotIn("ctx", tool.inputSchema.get("properties", {}))
        with self.assertRaisesRegex(AgentManagementDenied, "ALREADY_MOUNTED"):
            install_agent_management_tools(mcp, resolve_binding=lambda ctx: self.binding,
                                            principal=lambda: self.owner)

    async def test_binding_must_match_original_mcp_principal(self):
        self.binding = replace(self.binding, mcp_owner="another-mcp-owner")
        with self.assertRaisesRegex(AgentManagementDenied, "OWNER_MISMATCH"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls, [])

    async def test_wrong_create_reply_is_query_only_not_success(self):
        async def wrong_reply(method, path, payload):
            return {"name": "other-agent"}
        self.binding = replace(self.binding, invoke=wrong_reply)
        with self.assertRaisesRegex(AgentManagementDenied, "RESULT_MISMATCH_QUERY_ONLY"):
            await self.service.create_agent(None, self.request())

    async def test_bad_fence_cannot_invoke_same_effect_twice(self):
        async def double_invoke(action, key, digest, perform):
            await perform()
            return await perform()
        self.binding = replace(self.binding, invoke_once=double_invoke)
        with self.assertRaisesRegex(AgentManagementDenied, "ALREADY_ATTEMPTED"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls.count(("POST", "/api/agents")), 1)

    async def test_lost_reply_after_original_commit_is_not_retried(self):
        original_invoke = self.binding.invoke
        async def lose_reply(method, path, payload):
            await original_invoke(method, path, payload)
            raise TimeoutError("synthetic transport lost reply after commit")
        self.binding = replace(self.binding, invoke=lose_reply)
        with self.assertRaises(TimeoutError):
            await self.service.create_agent(None, self.request())
        with self.assertRaisesRegex(AgentManagementDenied, "UNKNOWN_QUERY_ONLY"):
            await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls.count(("POST", "/api/agents")), 1)
        self.binding = replace(self.binding, invoke=original_invoke)
        readback = await self.service.read_agent(None, "qa-dr-fixed")
        self.assertEqual(readback["name"], "qa-dr-fixed")

    async def test_receipt_query_never_calls_create_transport(self):
        async def query(action, key, digest):
            return {"status": "ATTEMPTED_UNKNOWN_QUERY_ONLY", "operation_id": "fixture-id"}
        self.binding = replace(self.binding, query_operation=query)
        result = await self.service.create_receipt(None, "qa-dr-fixed", "op1", "a" * 64)
        self.assertEqual(result["status"], "ATTEMPTED_UNKNOWN_QUERY_ONLY")
        self.assertEqual(self.calls, [])

    async def test_original_router_with_real_source_journal_survives_reopen(self):
        from deer_management_journal import OriginalManagementJournal
        spec = importlib.util.spec_from_file_location("original_source_io_for_router_test",
                                                    os.environ["DR_ORIGINAL_SOURCE_IO"])
        source_io = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(source_io)
        root = Path(self.temp.name) / "original-source-root"
        root.mkdir(mode=0o700)
        async def authorize():
            return self.allowed
        def journal():
            return OriginalManagementJournal(root, binding={"mcp_owner": self.owner,
                "gateway_user_id": self.user, "grant_id": "fixture-live-grant"},
                current=lambda: True, authorize=authorize, source_io=source_io)
        first_journal = journal()
        self.binding = replace(self.binding, invoke_once=first_journal,
                               query_operation=first_journal.query)
        created = await self.service.create_agent(None, self.request())
        restored = journal()
        self.binding = replace(self.binding, invoke_once=restored, query_operation=restored.query)
        recovered = await self.service.create_receipt(None, "qa-dr-fixed",
            created["operation_key"], created["payload_sha256"])
        self.assertEqual(recovered["status"], "CONFIRMED")
        self.assertEqual(recovered["result"]["agent"]["name"], "qa-dr-fixed")
        await self.service.create_agent(None, self.request())
        self.assertEqual(self.calls.count(("POST", "/api/agents")), 1)
        self.assertEqual((await self.service.read_agent(None, "qa-dr-fixed"))["name"], "qa-dr-fixed")


if __name__ == "__main__":
    unittest.main()
