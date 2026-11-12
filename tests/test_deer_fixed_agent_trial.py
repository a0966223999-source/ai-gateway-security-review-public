"""Bounded host-trial tests; identities/policy are explicit test fixtures."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore
from deerflow.persistence.base import Base

from deer_agent_management import AgentManagementDenied
from deer_fixed_agent_trial import OriginalHostTrial
from deer_management_test_scope import TEST_AGENT_NAME


class HostTrialTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        path = os.environ["DR_ORIGINAL_SOURCE_IO"]
        spec = importlib.util.spec_from_file_location("host_trial_original_io", path)
        self.io = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.io)
        self.db = self.root / "original.sqlite"
        engine = create_engine("sqlite:///" + str(self.db))
        Base.metadata.create_all(engine, tables=[AgentRow.__table__])
        engine.dispose()
        with sqlite3.connect(self.db) as db:
            db.execute("CREATE TABLE users(id TEXT,system_role TEXT,token_version INTEGER,needs_setup INTEGER,oauth_provider TEXT,oauth_id TEXT)")
            db.execute("INSERT INTO users VALUES('fixture-owner','admin',3,0,NULL,NULL)")
            db.execute("CREATE TABLE threads_meta(thread_id TEXT,user_id TEXT)")
            db.execute("INSERT INTO threads_meta VALUES('fixture-thread','fixture-owner')")
        trial = OriginalHostTrial.__new__(OriginalHostTrial)
        trial.root, trial.io, trial.database = self.root, self.io, self.db
        trial.store = SqlAgentStore("sqlite:///" + str(self.db))
        trial.user_id = "fixture-owner"
        trial.link_id = "c" * 64
        trial.link = {"thread_id": "fixture-thread", "token_version": 3,
                      "expires_at": time.time() - 1, "scope": "platform-checkpoint-only"}
        trial.link_raw = self.io.encode(trial.link)
        fd = self.io.private_directory(self.root)
        try:
            self.io.publish(fd, "account-link-" + trial.link_id + ".json", trial.link_raw)
        finally:
            os.close(fd)
        trial.account = SimpleNamespace(config={"owner": "fixture-mcp-owner", "generation": "d" * 32})
        trial.current = lambda: not trial.closed
        trial.closed, trial.started, trial.expires_at = False, None, None
        trial.approval_reference = "fixture-explicit-operator-approval-not-a-live-grant"
        trial.operation_key = "fixture-host-trial"
        self.trial = trial
        self.permissions = ["threads:write", "runs:create"]
        async def permissions(*args, **kwargs):
            return self.permissions
        self.policy = patch("app.gateway.authz.resolve_route_permissions", side_effect=permissions)
        self.policy.start()

    async def asyncTearDown(self):
        self.policy.stop()
        self.trial.store._Session.kw["bind"].dispose()
        self.temp.cleanup()

    async def test_original_store_create_read_and_scope_closed(self):
        result = await self.trial.run()
        self.assertTrue(result["definition_saved"])
        self.assertTrue(result["independent_readback"])
        self.assertFalse(result["mcp_management_bound"])
        self.assertFalse(result["identity_link_used_as_management_grant"])
        self.assertTrue(result["identity_link_expired"])
        self.assertEqual(result["test_window_seconds"], 600)
        self.assertTrue(self.trial.closed)
        self.assertTrue(self.trial.store.exists(TEST_AGENT_NAME, user_id="fixture-owner"))
        self.assertFalse(self.trial.store.exists(TEST_AGENT_NAME, user_id="other-owner"))
        self.assertEqual(len(list(self.root.glob("agent-operator-trial-*.closed.json"))), 1)

    async def test_token_version_change_rejects_before_write(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE users SET token_version=4")
        with self.assertRaisesRegex(AgentManagementDenied, "TOKEN_VERSION_DENIED"):
            await self.trial.run()
        self.assertFalse(self.trial.store.exists(TEST_AGENT_NAME, user_id="fixture-owner"))
        self.assertEqual(list(self.root.glob("agent-operator-trial-*")), [])

    async def test_other_thread_owner_rejects_before_write(self):
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE threads_meta SET user_id='another-owner'")
        with self.assertRaisesRegex(AgentManagementDenied, "OWNER_OR_TOKEN_VERSION_DENIED"):
            await self.trial.run()
        self.assertFalse(self.trial.store.exists(TEST_AGENT_NAME, user_id="fixture-owner"))

    async def test_current_policy_denial_prevents_write(self):
        self.permissions = []
        with self.assertRaisesRegex(AgentManagementDenied, "OWNER_POLICY_DENIED"):
            await self.trial.run()
        self.assertFalse(self.trial.store.exists(TEST_AGENT_NAME, user_id="fixture-owner"))

    async def test_existing_role_is_preserved(self):
        self.trial.store.create(TEST_AGENT_NAME, {"name": TEST_AGENT_NAME}, "original", user_id="fixture-owner")
        with self.assertRaisesRegex(AgentManagementDenied, "EXISTS_QUERY_ONLY_NO_OVERWRITE"):
            await self.trial.run()
        self.assertEqual(self.trial.store.get_soul(TEST_AGENT_NAME, user_id="fixture-owner"), "original")


if __name__ == "__main__":
    unittest.main()
