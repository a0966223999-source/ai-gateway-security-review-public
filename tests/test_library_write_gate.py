"""Offline ACL, CAS contract and no-replay tests for authorized Library writes."""
import json
import time
import unittest

from library_write_gate import (
    LibraryWriteDenied, OriginalLibraryWriteScope, authorized_status,
    authorized_write, request_digest, validate_request,
)

OWNER = "oauth:" + "1" * 64
CLIENT = "tpc_approved"
KEY = "lib-write-20261117-01"


class WriteGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.current = True
        self.commits = []
        self.owner = OWNER
        self.client = CLIENT
        self.source = "chatgpt-library"
        self.target = "f_007"
        self.expected = "v3"
        self.mode = "update"
        self.mismatch = None
        self.fail_after_commit = False
        self.revoke_on_commit = False
        self.stored_receipt = None
        self.readback_count = 0

        async def commit(request):
            self.commits.append(request)
            self.stored_receipt = self.receipt(request)
            if self.revoke_on_commit:
                self.current = False
            if self.fail_after_commit:
                raise TimeoutError("commit response lost")

        async def readback(operation_key):
            self.readback_count += 1
            return self.stored_receipt

        self.commit = commit
        self.readback = readback

    def receipt(self, request):
        return {
            "schema": "original-library-write-receipt-v1",
            "source": self.source, "owner": OWNER,
            "client_id": CLIENT,
            "operation": request["operation"],
            "target_ref": request["target_ref"],
            "expected_version": request["expected_version"],
            "operation_key": request["operation_key"],
            "ref": "f_created" if request["operation"] == "create" else self.target,
            "version": "v4",
            "status": "committed",
            "request_digest": request_digest(request),
        }

    async def resolve(self, ctx, owner, client_id, operation,
                      target_ref, expected_version, operation_key):
        return OriginalLibraryWriteScope(
            owner=self.owner,
            client_id=self.client,
            source=self.source,
            operation=self.mode if operation != "status" else "status",
            target_ref=self.target if operation != "status" else "status",
            expected_version=self.expected if operation != "status" else "status-only",
            operation_key=KEY,
            expires_at=time.time() + 60,
            is_current=lambda: self.current,
            commit_original=self.commit,
            receipt_original=self.readback,
        )

    async def write(self, mime_type="application/json", content='{"a": 1}',
                    payload_kind="text", operation="update", target="f_007",
                    expected_version="v3", name=""):
        return await authorized_write(
            self.resolve, lambda: OWNER, lambda: CLIENT, None,
            operation, target, expected_version, KEY,
            mime_type, content, payload_kind, name,
        )

    async def test_exact_version_json_update_succeeds_with_readback(self):
        r = await self.write()
        self.assertEqual(r["status"], "committed")
        self.assertEqual(self.readback_count, 1)
        self.assertEqual(self.commits[0]["expected_version"], "v3")

    async def test_create_in_authorized_parent(self):
        self.mode, self.target, self.expected = "create", "folder_01", "create-only"
        r = await self.write(
            mime_type="text/markdown", content="# note",
            operation="create", target="folder_01", expected_version="create-only",
            name="shared-note.md")
        self.assertEqual(r["ref"], "f_created")
        self.assertEqual(self.commits[0]["name"], "shared-note.md")

    async def test_prepared_image_staged_ref_contract(self):
        r = await self.write(
            mime_type="image/png", content="sealed_image_012",
            payload_kind="prepared")
        self.assertEqual(r["status"], "committed")
        self.assertEqual(self.commits[0]["payload_kind"], "prepared")

    async def test_wrong_client_denied_before_effect(self):
        self.client = "another_client"
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertEqual(self.commits, [])

    async def test_wrong_owner_denied_before_effect(self):
        self.owner = "oauth:" + "2" * 64
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertFalse(self.commits)

    async def test_wrong_source_rights_revoked(self):
        self.current = False
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertFalse(self.commits)

    async def test_scope_not_bound_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await authorized_write(None, lambda: OWNER, lambda: CLIENT, None,
                                   "update", "f_007", "v3", KEY,
                                   "text/plain", "content")
        self.assertFalse(self.commits)

    async def test_wrong_expected_version_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(expected_version="v2")
        self.assertFalse(self.commits)

    async def test_wrong_target_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(target="f_unapproved")
        self.assertFalse(self.commits)

    async def test_wrong_operation_key_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await authorized_write(self.resolve, lambda: OWNER, lambda: CLIENT,
                                   None, "update", "f_007", "v3",
                                   "changed_operation_key", "text/plain", "a")
        self.assertFalse(self.commits)

    async def test_json_must_be_valid(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(content="{invalid json")
        self.assertFalse(self.commits)

    async def test_text_larger_than_16kb_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(mime_type="text/plain", content="A" * 16385)
        self.assertFalse(self.commits)

    async def test_raw_binary_inline_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(mime_type="image/png", content="imagebytes")
        self.assertFalse(self.commits)

    async def test_stage_ref_not_path(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(mime_type="image/png", content="../../abc",
                             payload_kind="prepared")
        self.assertFalse(self.commits)

    async def test_create_disallows_path_traversal(self):
        self.mode, self.target, self.expected = "create", "folder_01", "create-only"
        with self.assertRaises(LibraryWriteDenied):
            await self.write(operation="create", target="folder_01",
                             expected_version="create-only", name="../bad.json")
        self.assertFalse(self.commits)

    async def test_delete_not_available(self):
        with self.assertRaises(LibraryWriteDenied):
            await self.write(operation="delete")
        self.assertFalse(self.commits)

    async def test_receipt_source_mismatch_denied(self):
        old = self.receipt
        def mismatch(request):
            return {**old(request), "source": "other"}
        self.receipt = mismatch
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertEqual(len(self.commits), 1)

    async def test_receipt_expected_version_mismatch_denied(self):
        old = self.receipt
        def mismatch(request):
            return {**old(request), "expected_version": "wrong"}
        self.receipt = mismatch
        with self.assertRaises(LibraryWriteDenied):
            await self.write()

    async def test_unknown_write_does_not_auto_retry(self):
        self.fail_after_commit = True
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertEqual(len(self.commits), 1)
        self.assertEqual(self.readback_count, 0)

    async def test_status_only_same_key_readback(self):
        request = validate_request("update", "f_007", "v3", KEY,
                                   "application/json", '{"a":1}', "text", "")
        self.stored_receipt = self.receipt(request)
        v = await authorized_status(self.resolve, lambda: OWNER,
                                    lambda: CLIENT, None, KEY)
        self.assertEqual(v["status"], "committed")
        self.assertEqual(self.commits, [])

    async def test_status_wrong_key_denied(self):
        with self.assertRaises(LibraryWriteDenied):
            await authorized_status(self.resolve, lambda: OWNER,
                                    lambda: CLIENT, None, "different-key")

    async def test_revoked_during_commit_denies_receipt(self):
        self.revoke_on_commit = True
        with self.assertRaises(LibraryWriteDenied):
            await self.write()
        self.assertEqual(len(self.commits), 1)
        self.assertEqual(self.readback_count, 0)

    async def test_scope_expired_denied(self):
        original = self.resolve
        async def expired(*args):
            s = await original(*args)
            return OriginalLibraryWriteScope(**{
                **s.__dict__, "expires_at": time.time() - 1})
        with self.assertRaises(LibraryWriteDenied):
            await authorized_write(expired, lambda: OWNER, lambda: CLIENT,
                                   None, "update", "f_007", "v3", KEY,
                                   "text/plain", "abc")
        self.assertFalse(self.commits)


if __name__ == "__main__":
    unittest.main()
