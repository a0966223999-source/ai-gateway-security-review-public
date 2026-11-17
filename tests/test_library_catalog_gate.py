"""Offline original Library bridge safety tests. Never call ChatGPT or Vercel."""
import unittest
from library_catalog_gate import (
    LibraryReadDenied, OriginalLibraryScope, authorized_read,
    authorized_search, validate_entry,
)

OWNER = "oauth:" + "b" * 64
REVISION = "a" * 64
ITEM = {"ref": "f_001", "name": "神經圖.png", "kind": "file",
        "mime_type": "image/png", "version": "3",
        "size_bytes": 123, "modified_at": "2026-11-17T00:00:00Z"}


class LibraryBridgeContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = OWNER
        self.current = True
        self.calls = []
        self.items = [dict(ITEM)]
        self.cursor = ""
        self.kind = "image_description"
        self.body = "A summary of the authorized diagram; not image bytes"
        self.return_version = "3"
        self.switch_owner_on_finish = False
        self.revoke_after_io = False

        async def search(query, cursor, limit):
            self.calls.append(("search", query, cursor, limit))
            if self.revoke_after_io:
                self.current = False
            return {"schema": "chatgpt-library-catalog-page-v1", "source": "chatgpt-library",
                    "owner": OWNER, "revision": REVISION, "items": self.items,
                    "next_cursor": self.cursor}

        async def read(ref, expected_version):
            self.calls.append(("read", ref, expected_version))
            if self.revoke_after_io:
                self.current = False
            return {"schema": "chatgpt-library-item-view-v1", "source": "chatgpt-library",
                    "owner": OWNER, "revision": REVISION, "ref": ref,
                    "version": self.return_version, "mime_type": "image/png",
                    "preview_kind": self.kind, "preview": self.body}

        self.scope = OriginalLibraryScope(
            owner=OWNER, revision=REVISION,
            is_current=lambda: self.current,
            search_original=search, read_original=read,
        )

    async def resolve(self, context, owner, action, ref):
        self.calls.append(("resolve", owner, action, ref))
        return self.scope

    def principal(self):
        return OWNER if not self.switch_owner_on_finish else "other"

    async def test_authorized_diagram_catalog(self):
        result = await authorized_search(self.resolve, self.principal, object(), "", "", 20)
        self.assertEqual(result["items"][0]["mime_type"], "image/png")
        self.assertEqual(self.calls[0][2], "catalog_search")

    async def test_authorized_binary_catalog(self):
        self.items = [{**ITEM, "name": "gateway-backup.zip",
                       "mime_type": "application/zip"}]
        result = await authorized_search(self.resolve, self.principal, None)
        self.assertEqual(result["items"][0]["mime_type"], "application/zip")

    async def test_authorized_page_continuation(self):
        self.cursor = "next_01"
        result = await authorized_search(self.resolve, self.principal, None, "圖", "old_01", 10)
        self.assertEqual(result["next_cursor"], "next_01")

    async def test_authorized_single_file_read(self):
        result = await authorized_read(self.resolve, self.principal, None, "f_001", "3")
        self.assertEqual(result["preview_kind"], "image_description")
        self.assertEqual(self.calls[0][-1], "f_001")

    async def test_binary_manifest_is_not_raw_file_access(self):
        self.kind, self.body = "binary_manifest", "ZIP: manifest only"
        result = await authorized_read(self.resolve, self.principal, None, "f_001", "3")
        self.assertEqual(result["preview"], "ZIP: manifest only")

    async def test_denies_without_current_read_rights(self):
        self.current = False
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None)
        self.assertFalse(any(x[0] == "search" for x in self.calls))

    async def test_denies_revocation_during_io(self):
        self.revoke_after_io = True
        with self.assertRaises(LibraryReadDenied):
            await authorized_read(self.resolve, self.principal, None, "f_001", "3")

    async def test_denies_wrong_owner_scope(self):
        self.scope = OriginalLibraryScope(
            owner="other", revision=REVISION,
            is_current=lambda: True,
            search_original=self.scope.search_original,
            read_original=self.scope.read_original)
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None)

    async def test_denies_self_reported_acl_token_in_metadata(self):
        self.items = [{**ITEM, "access_token": "never"}]
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None)

    async def test_denies_duplicate_file_refs(self):
        self.items = [dict(ITEM), dict(ITEM)]
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None)

    async def test_denies_arbitrary_path_in_item_read(self):
        with self.assertRaises(LibraryReadDenied):
            await authorized_read(self.resolve, self.principal, None, "../../secret", "3")
        self.assertFalse(self.calls)

    async def test_denies_stale_expected_version(self):
        with self.assertRaises(LibraryReadDenied):
            await authorized_read(self.resolve, self.principal, None, "f_001", "2")

    async def test_denies_binary_body_over_bound(self):
        self.body = "x" * 32769
        with self.assertRaises(LibraryReadDenied):
            await authorized_read(self.resolve, self.principal, None, "f_001", "3")

    async def test_denies_page_limit_exceeded(self):
        self.items = [dict(ITEM, ref="f_%03d" % i) for i in range(21)]
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None, limit=20)

    async def test_denies_missing_source_binding(self):
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(None, self.principal, None)
        self.assertFalse(self.calls)

    async def test_denies_client_supplied_bogus_cursor(self):
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None, cursor="/" * 1025)

    async def test_denies_non_string_filename(self):
        with self.assertRaises(LibraryReadDenied):
            validate_entry({**ITEM, "name": None})

    async def test_denies_invalid_revision(self):
        self.scope = OriginalLibraryScope(
            owner=OWNER, revision="not-a-sha",
            is_current=lambda: True,
            search_original=self.scope.search_original,
            read_original=self.scope.read_original)
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(self.resolve, self.principal, None)


if __name__ == "__main__":
    unittest.main()
