"""Native Library provider selection with no new Source/Issuer/Store."""
import asyncio
import hashlib
import json
import time
import unittest

from library_catalog_gate import OriginalLibraryScope, authorized_search
from library_original_host_binding import (
    LibraryNativeBindingDenied, OriginalLibraryHostBinding,
    library_mount_state, make_original_library_resolvers,
)
from library_write_gate import OriginalLibraryWriteScope, authorized_write, request_digest

OWNER = "oauth:" + "a" * 64
CLIENT = "tpc_approved"
REV = "b" * 64
KEY = "native_library_write_a1"


class NativeBindingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.active = True
        self.reader_calls = []
        self.writer_calls = []
        self.saved = None
        self.client_from_scope = CLIENT
        self.source_from_scope = "chatgpt-library"

        async def search(query, cursor, limit):
            self.reader_calls.append(("search", query, cursor, limit))
            return {"schema": "chatgpt-library-catalog-page-v1",
                    "source": "chatgpt-library", "owner": OWNER,
                    "revision": REV,
                    "items": [{"ref": "diagram001", "name": "diagram.png",
                               "kind": "file", "mime_type": "image/png",
                               "version": "v3"}], "next_cursor": ""}
        async def read(ref, ver):
            return {"schema": "chatgpt-library-item-view-v1",
                    "source": "chatgpt-library", "owner": OWNER,
                    "revision": REV, "ref": ref, "version": ver,
                    "mime_type": "image/png",
                    "preview_kind": "image_description", "preview": "bounded"}

        async def resolve_reader(ctx, owner, client_id, action, ref):
            self.reader_calls.append(("resolve", owner, client_id, action, ref))
            return OriginalLibraryScope(owner=OWNER, client_id=self.client_from_scope,
                revision=REV, is_current=lambda: self.active,
                search_original=search, read_original=read)

        async def commit(request):
            self.writer_calls.append(request)
            self.saved = {
                "schema": "original-library-write-receipt-v1",
                "source": self.source_from_scope, "owner": OWNER,
                "client_id": CLIENT, "operation": request["operation"],
                "target_ref": request["target_ref"],
                "expected_version": request["expected_version"],
                "operation_key": KEY, "ref": request["target_ref"],
                "version": "v4", "status": "committed",
                "request_digest": request_digest(request)}

        async def receipt(key):
            return self.saved

        async def resolve_writer(ctx, owner, client_id, op, ref, ver, key):
            return OriginalLibraryWriteScope(
                owner=OWNER, client_id=CLIENT, source=self.source_from_scope,
                operation=op, target_ref=ref, expected_version=ver,
                operation_key=key, expires_at=time.time()+40,
                is_current=lambda: self.active,
                commit_original=commit, receipt_original=receipt)

        self.binding=OriginalLibraryHostBinding(
            source="chatgpt-library", generation=REV,
            is_current=lambda: self.active,
            resolve_reader=resolve_reader, resolve_writer=resolve_writer)

    async def test_missing_capability_is_not_bound(self):
        a,b=make_original_library_resolvers(None)
        self.assertIsNone(a)
        self.assertIsNone(b)
        self.assertEqual(library_mount_state(a,b)["state"], "NOT_BOUND")

    async def test_rejects_dictionary_and_untrusted_self_report(self):
        for bogus in ({}, {"source":"chatgpt-library","generation":REV},
                      "owner-says-yes", True):
            with self.assertRaises(LibraryNativeBindingDenied):
                make_original_library_resolvers(bogus)

    async def test_rejects_different_source_and_invalid_generation(self):
        from dataclasses import replace
        for changed in (replace(self.binding,source="google-drive"),
                        replace(self.binding,generation="not-hash")):
            with self.assertRaises(LibraryNativeBindingDenied):
                make_original_library_resolvers(changed)

    async def test_denies_revoked_binding_before_mount(self):
        self.active=False
        with self.assertRaises(LibraryNativeBindingDenied):
            make_original_library_resolvers(self.binding)

    async def test_authorized_catalog_from_original_port(self):
        reader,writer=make_original_library_resolvers(self.binding)
        page=await authorized_search(reader,lambda:OWNER,lambda:CLIENT,None,"diagram","",10)
        self.assertEqual(page["items"][0]["name"],"diagram.png")
        self.assertTrue(writer is not None)

    async def test_cross_client_not_authorized_despite_same_owner(self):
        self.client_from_scope="tpc_other"
        read,_=make_original_library_resolvers(self.binding)
        from library_catalog_gate import LibraryReadDenied
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(read,lambda:OWNER,lambda:CLIENT,None)
        self.assertFalse(any(c[0]=="search" for c in self.reader_calls))

    async def test_host_revoked_after_mount_denies_read(self):
        read,_=make_original_library_resolvers(self.binding)
        self.active=False
        from library_catalog_gate import LibraryReadDenied
        with self.assertRaises(LibraryReadDenied):
            await authorized_search(read,lambda:OWNER,lambda:CLIENT,None)

    async def test_host_write_delegates_only_to_original_atomic_writer(self):
        _,write=make_original_library_resolvers(self.binding)
        result=await authorized_write(write,lambda:OWNER,lambda:CLIENT,None,
            "update","fileA","v3",KEY,"application/json",'{"notes":1}')
        self.assertEqual(result["status"],"committed")
        self.assertEqual(len(self.writer_calls),1)

    async def test_source_mismatch_denies_write_before_effect(self):
        self.source_from_scope="google-drive"
        _,write=make_original_library_resolvers(self.binding)
        from library_write_gate import LibraryWriteDenied
        with self.assertRaises(LibraryWriteDenied):
            await authorized_write(write,lambda:OWNER,lambda:CLIENT,None,
                "update","fileA","v3",KEY,"text/plain","no")
        self.assertFalse(self.writer_calls)

    async def test_original_binding_without_writer_is_read_only(self):
        from dataclasses import replace
        reader,writer=make_original_library_resolvers(
            replace(self.binding,resolve_writer=None))
        self.assertTrue(callable(reader))
        self.assertIsNone(writer)
        self.assertFalse(library_mount_state(reader,writer)["write_resolver_registered"])

    async def test_registration_never_claims_live_source(self):
        read,write=make_original_library_resolvers(self.binding)
        s=library_mount_state(read,write)
        self.assertEqual(s["state"],"RESOLVER_REGISTERED_NOT_LIVE_VERIFIED")
        self.assertFalse(s["source_live_verified"])
        self.assertFalse(s["native_chatgpt_library_api_available"])
        self.assertFalse(s["automatic_dispatch"])


if __name__=="__main__":
    unittest.main()
