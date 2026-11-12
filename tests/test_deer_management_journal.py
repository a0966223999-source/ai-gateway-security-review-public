"""Real Source filesystem/fence tests; authorization is an explicit fixture."""

import asyncio
import importlib.util
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from deer_agent_management import AgentManagementDenied
from deer_management_journal import OriginalManagementJournal


class JournalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        source_path = os.environ["DR_ORIGINAL_SOURCE_IO"]
        spec = importlib.util.spec_from_file_location("isolated_original_source_io", source_path)
        self.io = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.io)
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.allowed, self.running, self.effects = True, True, 0
        self.binding = {"mcp_owner": "fixture-owner", "gateway_user_id": "fixture-user", "grant_id": "fixture-grant"}
        self.digest = "a" * 64
        self.journal = self.reopen()

    async def asyncTearDown(self):
        self.temp.cleanup()

    def reopen(self, **changes):
        async def authorize():
            return self.allowed
        return OriginalManagementJournal(self.root,
            binding={**self.binding, **changes}, current=lambda: self.running,
            authorize=authorize, source_io=self.io)

    async def effect(self):
        self.effects += 1
        return {"name": "qa-dr-fixed", "status": "REGISTRY_SAVED_READBACK_REQUIRED"}

    async def test_completed_result_survives_new_journal_instance(self):
        first = await self.journal("create", "op1", self.digest, self.effect)
        second = await self.reopen()("create", "op1", self.digest, self.effect)
        self.assertEqual(first, second)
        self.assertEqual(self.effects, 1)
        self.assertEqual((await self.reopen().query("create", "op1", self.digest))["status"], "CONFIRMED")
        for path in self.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    async def test_crash_after_commit_stays_unknown_after_restart(self):
        async def lost():
            await self.effect()
            raise TimeoutError("synthetic lost transport reply")
        with self.assertRaises(TimeoutError):
            await self.journal("create", "op1", self.digest, lost)
        with self.assertRaisesRegex(AgentManagementDenied, "UNKNOWN_QUERY_ONLY"):
            await self.reopen()("create", "op1", self.digest, self.effect)
        self.assertEqual(self.effects, 1)

    async def test_same_key_different_digest_conflicts(self):
        await self.journal("create", "op1", self.digest, self.effect)
        with self.assertRaisesRegex(AgentManagementDenied, "OPERATION_CONFLICT"):
            await self.reopen()("create", "op1", "b" * 64, self.effect)
        self.assertEqual(self.effects, 1)

    async def test_concurrent_reservation_does_not_repeat_effect(self):
        entered, resume = asyncio.Event(), asyncio.Event()
        async def paused():
            entered.set()
            await resume.wait()
            return await self.effect()
        first = asyncio.create_task(self.journal("create", "op1", self.digest, paused))
        await entered.wait()
        with self.assertRaisesRegex(AgentManagementDenied, "UNKNOWN_QUERY_ONLY"):
            await self.reopen()("create", "op1", self.digest, self.effect)
        resume.set()
        await first
        self.assertEqual(self.effects, 1)

    async def test_owner_cannot_query_another_owners_receipt(self):
        await self.journal("create", "op1", self.digest, self.effect)
        other = self.reopen(gateway_user_id="other-user")
        self.assertEqual((await other.query("create", "op1", self.digest))["status"], "NOT_FOUND_QUERY_ONLY")
        self.assertEqual(self.effects, 1)

    async def test_revoke_or_stop_prevents_new_effect(self):
        self.allowed = False
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
            await self.journal("create", "op1", self.digest, self.effect)
        self.allowed, self.running = True, False
        with self.assertRaisesRegex(AgentManagementDenied, "AUTHORITY_DENIED"):
            await self.journal("create", "op1", self.digest, self.effect)
        self.assertEqual(self.effects, 0)

    async def test_receipt_save_failure_is_unknown_not_replay(self):
        original = self.io.publish
        def fail_receipt(fd, name, raw):
            if name.startswith("agent-receipt-"):
                raise OSError("synthetic receipt fsync failure")
            return original(fd, name, raw)
        with patch.object(self.io, "publish", side_effect=fail_receipt):
            with self.assertRaisesRegex(AgentManagementDenied, "RESULT_UNKNOWN_QUERY_ONLY"):
                await self.journal("create", "op1", self.digest, self.effect)
        with self.assertRaisesRegex(AgentManagementDenied, "UNKNOWN_QUERY_ONLY"):
            await self.reopen()("create", "op1", self.digest, self.effect)
        self.assertEqual(self.effects, 1)

    async def test_readonly_query_does_not_create_records(self):
        self.assertEqual((await self.journal.query("create", "op1", self.digest))["status"], "NOT_FOUND_QUERY_ONLY")
        self.assertEqual(list(self.root.iterdir()), [])

    async def test_symlink_receipt_is_rejected(self):
        await self.journal("create", "op1", self.digest, self.effect)
        receipt = next(self.root.glob("agent-receipt-*.json"))
        outside = self.root / "outside.json"
        receipt.rename(outside)
        receipt.symlink_to(outside)
        with self.assertRaises(OSError):
            await self.reopen().query("create", "op1", self.digest)

    async def test_replaced_source_root_is_rejected(self):
        moved = self.root.with_name(self.root.name + "-old")
        self.root.rename(moved)
        try:
            self.root.mkdir(mode=0o700)
            with self.assertRaisesRegex(AgentManagementDenied, "ROOT_CHANGED"):
                await self.journal("create", "op1", self.digest, self.effect)
            self.assertEqual(self.effects, 0)
        finally:
            self.root.rmdir()
            moved.rename(self.root)


if __name__ == "__main__":
    unittest.main()
