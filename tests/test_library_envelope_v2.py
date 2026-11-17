import hashlib
import json
import unittest

import library_envelope_v2 as L

NOW = 1794890000
TEST_ID = "grokbot-library-handshake-20261117-277529"
THREAD = "thr_" + "0123456789abcdef" * 2
SHA = hashlib.sha256(b"x").hexdigest()


def env(**over):
    base = {"s": "dle/2", "t": TEST_ID, "k": TEST_ID + "-a2",
            "f": "grok-bot", "r": "gpt-work:" + THREAD, "a": "read",
            "fr": "library:file_0123456789abcdef@v3", "v": "v3",
            "ia": NOW, "ttl": 600, "st": "req", "pr": "-"}
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def inline(text, **over):
    fields = dict(a="create", fr="library:parent_isolated_canary@create-only",
                  v="create-only", nm="grokbot-canary-20261117.json",
                  m="application/json", pr="in:" + L.sha256_text(text), p=text)
    fields.update(over)
    return env(**fields)


class EnvelopeShape(unittest.TestCase):
    def test_read_request_roundtrip_under_800(self):
        msg = L.encode(env(), now=NOW)
        self.assertLessEqual(len(msg), L.RECOMMENDED_LIMIT)
        self.assertEqual(L.decode(msg, now=NOW), env())

    def test_all_requested_fields_present(self):
        names = {L.LONG_NAMES[k] for k in L.REQUIRED}
        for f in ("schema", "test_id", "operation_key", "sender", "receiver", "action",
                  "file_ref", "version", "ttl_seconds", "status", "payload_ref"):
            self.assertIn(f, names)

    def test_unknown_or_missing_field_denied(self):
        with self.assertRaises(L.EnvelopeError):
            L.validate(env(extra="1"), now=NOW)
        e = env(); del e["st"]
        with self.assertRaises(L.EnvelopeError):
            L.validate(e, now=NOW)

    def test_hard_limit_enforced(self):
        big = inline("{\"a\":\"" + "lorem " * 160 + "\"}")
        with self.assertRaisesRegex(L.EnvelopeError, "ENVELOPE_TOO_LONG"):
            L.encode(big, now=NOW, limit=L.HARD_LIMIT)

    def test_recommended_limit_default(self):
        mid = inline("{\"a\":\"" + "lorem " * 85 + "\"}")
        with self.assertRaisesRegex(L.EnvelopeError, "ENVELOPE_TOO_LONG"):
            L.encode(mid, now=NOW)
        self.assertLessEqual(len(L.encode(mid, now=NOW, limit=1000)), 1000)

    def test_limit_cannot_exceed_hard(self):
        with self.assertRaises(L.EnvelopeError):
            L.encode(env(), now=NOW, limit=1024)

    def test_non_canonical_or_duplicate_rejected(self):
        msg = L.encode(env(), now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.decode(json.dumps(env(), indent=1), now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.decode(msg[:-1] + ',"s":"dle/2"}', now=NOW)

    def test_ttl_expiry_and_future(self):
        with self.assertRaisesRegex(L.EnvelopeError, "EXPIRED"):
            L.validate(env(), now=NOW + 600)
        with self.assertRaisesRegex(L.EnvelopeError, "FUTURE"):
            L.validate(env(ia=NOW + 3600), now=NOW)
        with self.assertRaisesRegex(L.EnvelopeError, "TTL"):
            L.validate(inline("{}", ttl=901), now=NOW)

    def test_identity_never_verified_by_envelope(self):
        s = L.validate(env(), now=NOW)
        self.assertFalse(s["identity_verified"])
        self.assertFalse(s["library_authorized"])

    def test_result_needs_reply_ref(self):
        with self.assertRaises(L.EnvelopeError):
            L.validate(env(st="ok"), now=NOW)
        L.validate(env(st="ok", re="a" * 64, h="b" * 64), now=NOW)


class Semantics(unittest.TestCase):
    def test_search_and_status_shapes(self):
        L.validate(env(a="search", fr="catalog", v="none", q="grokbot-canary", n=10), now=NOW)
        L.validate(env(a="status", fr="status", v="none"), now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.validate(env(a="status", fr="library:x@v1", v="none"), now=NOW)

    def test_read_requires_pinned_matching_version(self):
        with self.assertRaises(L.EnvelopeError):
            L.validate(env(v="v4"), now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.validate(env(v="none"), now=NOW)

    def test_create_only(self):
        L.validate(inline('{"canary":1}'), now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.validate(inline('{"canary":1}', v="v1"), now=NOW)

    def test_update_requires_original_version_compare(self):
        txt = '{"canary":2}'
        ok = env(a="update", fr="library:canary_file@v3", v="v3", m="application/json",
                 pr="in:" + L.sha256_text(txt), p=txt)
        L.validate(ok, now=NOW)
        bad = dict(ok, v="v4")
        with self.assertRaisesRegex(L.EnvelopeError, "ORIGINAL_VERSION"):
            L.validate(bad, now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.validate(dict(ok, v="create-only"), now=NOW)

    def test_inline_sha_mismatch(self):
        e = inline('{"a":1}')
        e["p"] = '{"a":2}'
        with self.assertRaisesRegex(L.EnvelopeError, "SHA256"):
            L.validate(e, now=NOW)

    def test_binary_write_needs_controlled_ref(self):
        e = env(a="create", fr="library:parent@create-only", v="create-only", nm="graph.png",
                m="image/png", pr="sealed:prep_graph_01@" + SHA)
        L.validate(e, now=NOW)
        with self.assertRaises(L.EnvelopeError):
            L.validate(dict(e, pr="in:" + SHA, p="x"), now=NOW)


class FileRefs(unittest.TestCase):
    def test_valid_refs(self):
        self.assertEqual(L.parse_file_ref("library:file_1@v3")["version"], "v3")
        self.assertEqual(L.parse_file_ref("drive:1AbCdEfGhIjKlMn@" + SHA)["provider"], "drive")
        self.assertEqual(L.parse_file_ref("dropbox:id:sBBMuAxz79cAAAAAAAAADA@" + SHA)["id"],
                         "id:sBBMuAxz79cAAAAAAAAADA")
        self.assertEqual(L.parse_file_ref("sealed:prep_1@" + SHA)["provider"], "sealed")

    def test_invalid_refs(self):
        for bad in ("https://drive.google.com/x", "library:../etc@v1", "library:a/b@v1",
                    "drive:short@" + SHA, "drive:1AbCdEfGhIjKlMn@v3", "s3:bucket@v1",
                    "library:x", "/tmp/a.zip", "C:\\a.zip", ""):
            with self.assertRaises(L.EnvelopeError, msg=bad):
                L.parse_file_ref(bad)

    def test_credentials_and_raw_content_rejected(self):
        bads = ["Bearer abcdefghijklmnop", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.x",
                "ghp_" + "a" * 36, "password=hunter2", "see https://example.com",
                "data:image/png;base64,AAAA", "QUJD" * 60, "-----BEGIN RSA PRIVATE KEY-----",
                "cookie: sid=1", "\x00\x01"]
        for b in bads:
            with self.assertRaises(L.EnvelopeError, msg=b[:20]):
                L.scan_forbidden(b)
        txt = json.dumps({"note": "ghp_" + "a" * 36})
        with self.assertRaisesRegex(L.EnvelopeError, "FORBIDDEN"):
            L.validate(inline(txt), now=NOW)


class Chunking(unittest.TestCase):
    TEXT = ("# 隔離測試檔 canary\n" + "中文內容與 ASCII mixed \"quotes\" \\ back\n" * 80)

    def test_split_all_parts_fit_and_reassemble(self):
        key = TEST_ID + "-a1"
        ref, parts = L.split_text(self.TEXT, key)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= L.RECOMMENDED_LIMIT for p in parts))
        r = L.Reassembler(ref, key, deadline=NOW + 600)
        for p in reversed(parts):
            r.add(p)
        out = r.result(now=NOW)
        self.assertEqual(out["status"], "COMPLETE")
        self.assertEqual(out["text"], self.TEXT)

    def test_missing_part_is_query_only_never_resend(self):
        key = TEST_ID + "-a1"
        ref, parts = L.split_text(self.TEXT, key)
        r = L.Reassembler(ref, key, deadline=NOW + 600)
        for p in parts[1:]:
            r.add(p)
        waiting = r.result(now=NOW)
        self.assertEqual(waiting["status"], "WAITING")
        self.assertEqual(waiting["missing"], [1])
        self.assertEqual(waiting["query_inbox_keys"], [key + "-p01"])
        self.assertFalse(waiting["resend"])
        late = r.result(now=NOW + 601)
        self.assertEqual(late["status"], "UNKNOWN_QUERY_ONLY")
        self.assertNotIn("text", late)

    def test_tampered_part_rejected(self):
        key = TEST_ID + "-a1"
        ref, parts = L.split_text(self.TEXT, key)
        c = json.loads(parts[0]); c["x"] = c["x"][:-1] + "Z"
        with self.assertRaisesRegex(L.EnvelopeError, "PART_SHA256|CHUNK_SHAPE"):
            L.parse_chunk(L.canonical(c))
        c["h"] = L.sha256_text(c["x"])  # attacker fixes part hash; whole hash catches it
        r = L.Reassembler(ref, key, deadline=NOW + 600)
        r.add(L.canonical(c))
        for p in parts[1:]:
            r.add(p)
        with self.assertRaisesRegex(L.EnvelopeError, "WHOLE_SHA256"):
            r.result(now=NOW)

    def test_foreign_or_conflicting_part_rejected(self):
        key = TEST_ID + "-a1"
        ref, parts = L.split_text(self.TEXT, key)
        _, other = L.split_text(self.TEXT + "!", key)
        r = L.Reassembler(ref, key, deadline=NOW + 600)
        with self.assertRaisesRegex(L.EnvelopeError, "FOREIGN"):
            r.add(other[0])
        r.add(parts[0]); r.add(parts[0])  # exact duplicate tolerated
        c = json.loads(parts[0]); c["x"] = "different"; c["h"] = L.sha256_text("different")
        with self.assertRaisesRegex(L.EnvelopeError, "CONFLICT"):
            r.add(L.canonical(c))

    def test_chunk_payload_ref_in_envelope_and_pr61_mapping(self):
        key = TEST_ID + "-a1"
        text = json.dumps({"canary": "canary text " * 110})
        ref, parts = L.split_text(text, key)
        e = env(a="create", k=key, fr="library:parent_isolated_canary@create-only",
                v="create-only", nm="grokbot-canary-20261117.json",
                m="application/json", pr=ref)
        L.encode(e, now=NOW)
        req = L.to_pr61_request(e, text)
        self.assertEqual(set(req), {"schema", "source", "operation", "operation_key",
                                    "target_ref", "expected_version", "name", "mime_type",
                                    "payload_kind", "content"})
        self.assertEqual(req["operation"], "create")
        self.assertEqual(req["expected_version"], "create-only")
        with self.assertRaises(L.EnvelopeError):
            L.to_pr61_request(e, text + " ")

    def test_too_many_parts(self):
        with self.assertRaises(L.EnvelopeError):
            L.split_text("y" * 40000, TEST_ID)


class Alignment(unittest.TestCase):
    def test_pr61_operation_names(self):
        self.assertEqual(L.to_pr61_request(env(a="search", fr="catalog", v="none"))["operation"], "catalog")
        r = L.to_pr61_request(env())
        self.assertEqual((r["operation"], r["target_ref"], r["expected_version"]),
                         ("read", "file_0123456789abcdef", "v3"))
        self.assertEqual(L.PR62_OPERATION["read"], "file_read")

    def test_prepared_maps_to_sealed_handle(self):
        e = env(a="create", fr="library:parent@create-only", v="create-only", nm="g.zip",
                m="application/zip", pr="sealed:prep_zip_01@" + SHA)
        r = L.to_pr61_request(e)
        self.assertEqual((r["payload_kind"], r["content"]), ("prepared", "prep_zip_01"))
        e2 = dict(e, pr="drive:1AbCdEfGhIjKlMn@" + SHA)
        with self.assertRaisesRegex(L.EnvelopeError, "SEALED"):
            L.to_pr61_request(e2)


class UnknownDiscipline(unittest.TestCase):
    def test_single_key_and_query_only(self):
        j = L.OperationJournal()
        e = inline('{"canary":1}', k=TEST_ID + "-a1")
        j.begin(e)
        with self.assertRaisesRegex(L.EnvelopeError, "KEY_ALREADY_USED"):
            j.begin(e)
        j.settle(e["k"], "UNKNOWN_QUERY_ONLY")
        self.assertEqual(j.next_step(e["k"]), "QUERY_ORIGINAL_KEY")
        with self.assertRaisesRegex(L.EnvelopeError, "QUERY_ORIGINAL_KEY"):
            j.begin(dict(e, k=TEST_ID + "-a1b"))  # no fresh key for same intent
        j.settle(e["k"], "committed")
        self.assertEqual(j.next_step(e["k"]), "DONE")

    def test_derived_transport_keys(self):
        self.assertEqual(L.inbox_key_for("abc", "req"), "abc")
        self.assertEqual(L.inbox_key_for("abc", "query", 1), "abc-q01")
        with self.assertRaises(L.EnvelopeError):
            L.inbox_key_for("k" * 79, "result", 1)


class VersionLength(unittest.TestCase):
    """VERSION accepts 1–128 of [A-Za-z0-9._-]; Drive/Dropbox refs stay 64-hex."""

    DRIVE_ID = "1AbCdEfGhIjKlMn"
    DROPBOX_ID = "id:sBBMuAxz79cAAAAAAAAADA"

    def test_drive_read_64hex_roundtrip(self):
        ver = hashlib.sha256(b"drive-rev").hexdigest()
        self.assertEqual(len(ver), 64)
        e = env(fr="drive:" + self.DRIVE_ID + "@" + ver, v=ver)
        msg = L.encode(e, now=NOW)
        self.assertLessEqual(len(msg), L.RECOMMENDED_LIMIT)
        self.assertEqual(L.decode(msg, now=NOW), e)
        parsed = L.validate(e, now=NOW)["file_ref"]
        self.assertEqual((parsed["provider"], parsed["version"]), ("drive", ver))

    def test_dropbox_read_64hex_roundtrip(self):
        ver = hashlib.sha256(b"dropbox-rev").hexdigest()
        self.assertEqual(len(ver), 64)
        e = env(fr="dropbox:" + self.DROPBOX_ID + "@" + ver, v=ver)
        msg = L.encode(e, now=NOW)
        self.assertLessEqual(len(msg), L.RECOMMENDED_LIMIT)
        self.assertEqual(L.decode(msg, now=NOW), e)
        parsed = L.validate(e, now=NOW)["file_ref"]
        self.assertEqual((parsed["provider"], parsed["version"]), ("dropbox", ver))

    def test_version_128_roundtrip(self):
        ver = "a" * 128
        e = env(fr="library:file_0123456789abcdef@" + ver, v=ver)
        msg = L.encode(e, now=NOW)
        self.assertEqual(L.decode(msg, now=NOW)["v"], ver)

    def test_version_129_rejected(self):
        with self.assertRaisesRegex(L.EnvelopeError, "VERSION_INVALID"):
            L.validate(env(v="a" * 129), now=NOW)

    def test_version_disallowed_characters_rejected(self):
        for bad in ("v 1", "v/1", "v:1", "v+1", ".v1", "-v1", "_v1", "v1é"):
            with self.assertRaisesRegex(L.EnvelopeError, "VERSION_INVALID", msg=bad):
                L.validate(env(v=bad), now=NOW)

    def test_drive_ref_non_64hex_version_rejected(self):
        for ver in ("v3", "ab" * 31, "ab" * 32 + "c", "A" * 64, "g" * 64):
            ref = "drive:" + self.DRIVE_ID + "@" + ver
            with self.assertRaisesRegex(L.EnvelopeError, "FILE_REF_INVALID", msg=ver):
                L.parse_file_ref(ref)
        with self.assertRaisesRegex(L.EnvelopeError, "FILE_REF_INVALID"):
            L.validate(env(fr="drive:" + self.DRIVE_ID + "@v3", v="v3"), now=NOW)

    def test_read_version_mismatch_rejected(self):
        pinned = hashlib.sha256(b"pinned").hexdigest()
        other = hashlib.sha256(b"other").hexdigest()
        drive = env(fr="drive:" + self.DRIVE_ID + "@" + pinned, v=other)
        dropbox = env(fr="dropbox:" + self.DROPBOX_ID + "@" + pinned, v=other)
        library = env(fr="library:file_0123456789abcdef@v3", v="v4")
        for e in (drive, dropbox, library):
            with self.assertRaisesRegex(L.EnvelopeError, "READ_NEEDS_PINNED_VERSION"):
                L.validate(e, now=NOW)

    def test_empty_version_rejected(self):
        with self.assertRaisesRegex(L.EnvelopeError, "VERSION_INVALID"):
            L.validate(env(v=""), now=NOW)


class MeasuredSizes(unittest.TestCase):
    def test_typical_and_worst_case(self):
        typical = L.encode(env(st="ok", re="a" * 64, h="b" * 64), now=NOW)
        self.assertLessEqual(len(typical), 800)
        worst = {"s": "dle/2", "t": "T" * 64, "k": "K" * 80, "f": "a" * 16 + ":" + "b" * 48,
                 "r": "c" * 16 + ":" + "d" * 48, "a": "update",
                 "fr": "library:" + "e" * 96 + "@" + "f" * 48, "v": "f" * 48,
                 "ia": NOW, "ttl": 900, "st": "UNKNOWN_QUERY_ONLY",
                 "pr": "sealed:" + "g" * 64 + "@" + "0" * 64, "m": "application/octet-stream",
                 "re": "1" * 64, "h": "2" * 64}
        L.validate(worst, now=NOW, limit=L.HARD_LIMIT)
        self.assertLessEqual(len(L.canonical(worst)), L.HARD_LIMIT)


if __name__ == "__main__":
    unittest.main()
