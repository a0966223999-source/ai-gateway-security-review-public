"""Deer Library mailbox envelope v2: compact, length-bounded, chunked, ref-only.

Pure, dependency-free helper for messages carried by the ORIGINAL formal
mailbox (``gateway_inbox_send`` on ``gateway_internal_v1``). It is NOT a
Gateway, Store, Worker, Issuer, signer or authority:

* nothing here proves sender/receiver identity (``identity_verified`` is
  always False; trust comes only from original host attestation, PR #61/#62);
* nothing here grants Library rights (unbound Source stays DENY);
* nothing here sends, retries or persists anything.

Field names align with PR #7 tools (library_catalog_search / library_item_read
/ library_item_create / library_item_update / library_write_status) and with
PR #61 ``deer-library-original-request-v1`` / PR #62 host-port claims.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

SCHEMA = "dle/2"            # deer-library-envelope-v2
CHUNK_SCHEMA = "dlc/2"      # deer-library-chunk-v2
HARD_LIMIT = 1000           # owner hard limit (server limit is 1024 chars)
RECOMMENDED_LIMIT = 800     # owner recommended limit
SERVER_LIMIT = 1024         # inbox.py: 1 <= len(message) <= 1024
MAX_PARTS = 32
MAX_TTL = 3600
MAX_WRITE_TTL = 900

# compact key -> long name (documentation / debugging only)
LONG_NAMES = {
    "s": "schema", "t": "test_id", "k": "operation_key", "f": "sender",
    "r": "receiver", "a": "action", "fr": "file_ref", "v": "version",
    "ia": "issued_at", "ttl": "ttl_seconds", "st": "status",
    "pr": "payload_ref", "p": "inline_payload", "m": "mime_type",
    "nm": "name", "q": "query", "c": "cursor", "n": "limit",
    "re": "in_reply_to_event_id", "h": "request_digest",
}
REQUIRED = ("s", "t", "k", "f", "r", "a", "fr", "v", "ia", "ttl", "st", "pr")
OPTIONAL = ("p", "m", "nm", "q", "c", "n", "re", "h")

ACTIONS = ("search", "read", "create", "update", "status")
WRITES = ("create", "update")
REQUEST_STATUS = "req"
RESULT_STATUS = ("ok", "committed", "pending", "denied", "NOT_BOUND",
                 "UNKNOWN_QUERY_ONLY")
# PR #61 operation names (PR #62 uses catalog_search / file_read).
PR61_OPERATION = {"search": "catalog", "read": "read", "create": "create",
                  "update": "update", "status": "status"}
PR62_OPERATION = {"search": "catalog_search", "read": "file_read",
                  "create": "create", "update": "update", "status": "status"}
TEXT_TYPES = frozenset({"text/plain", "text/markdown", "application/json",
                        "text/x-python", "text/yaml", "application/yaml",
                        "text/csv"})
PREPARED_TYPES = frozenset({"image/png", "image/jpeg", "image/webp",
                            "image/svg+xml", "application/pdf",
                            "application/zip", "application/octet-stream"})

KEY = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")          # inbox identifier
TEST_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
AGENT = re.compile(r"[a-z0-9][a-z0-9_-]{0,15}(:[A-Za-z0-9_-]{1,48})?\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,47}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

FILE_REF = {
    # provider: (id pattern, version pattern)
    "library": (re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z"), VERSION),
    "drive": (re.compile(r"[A-Za-z0-9_-]{10,96}\Z"), HEX64),
    "dropbox": (re.compile(r"id:[A-Za-z0-9_-]{1,64}\Z"), HEX64),
    "sealed": (re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z"), HEX64),
}
SPECIAL_REFS = ("catalog", "status")

# Raw content / credential smells. Mailbox carries references only.
FORBIDDEN = [
    re.compile(r"[a-z][a-z0-9+.-]*://", re.I),            # any URL
    re.compile(r"\bdata:[a-z]+/", re.I),                    # data URI
    re.compile(r"\bbearer\s+\S", re.I),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|secret|cookie|set-cookie|"
               r"access_token|refresh_token|client_secret|api_key)\s*[:=]"),
    re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),                # base64 blob
    re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]"),             # binary control
]


class EnvelopeError(ValueError):
    pass


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scan_forbidden(text: str) -> None:
    for pattern in FORBIDDEN:
        if pattern.search(text):
            raise EnvelopeError("FORBIDDEN_RAW_OR_CREDENTIAL_CONTENT")


def parse_file_ref(ref: str) -> dict:
    """``library:<id>@<version>``, ``drive:<fileId>@<sha256>``,
    ``dropbox:id:<id>@<sha256>``, ``sealed:<handle>@<sha256>``, or the PR #61
    special targets ``catalog`` / ``status``, or ``-`` (none)."""
    if not isinstance(ref, str) or not 1 <= len(ref) <= 220:
        raise EnvelopeError("FILE_REF_INVALID")
    if ref == "-" or ref in SPECIAL_REFS:
        return {"provider": ref, "id": ref, "version": None}
    provider, sep, rest = ref.partition(":")
    if not sep or provider not in FILE_REF or "@" not in rest:
        raise EnvelopeError("FILE_REF_INVALID")
    ident, _, version = rest.rpartition("@")
    id_re, ver_re = FILE_REF[provider]
    if not id_re.fullmatch(ident) or not ver_re.fullmatch(version):
        raise EnvelopeError("FILE_REF_INVALID")
    return {"provider": provider, "id": ident, "version": version}


def parse_payload_ref(ref: str) -> dict:
    """``-`` none | ``in:<sha256>`` inline ``p`` | ``ck:<sha256>:<total>``
    chunked text | a controlled file_ref (binary/image/ZIP/graph)."""
    if ref == "-":
        return {"kind": "none"}
    if isinstance(ref, str) and ref.startswith("in:") and HEX64.fullmatch(ref[3:]):
        return {"kind": "inline", "sha256": ref[3:]}
    if isinstance(ref, str) and ref.startswith("ck:"):
        parts = ref[3:].split(":")
        if (len(parts) == 2 and HEX64.fullmatch(parts[0])
                and parts[1].isdigit() and 2 <= int(parts[1]) <= MAX_PARTS
                and str(int(parts[1])) == parts[1]):
            return {"kind": "chunked", "sha256": parts[0], "total": int(parts[1])}
        raise EnvelopeError("PAYLOAD_REF_INVALID")
    try:
        parsed = parse_file_ref(ref)
    except EnvelopeError:
        raise EnvelopeError("PAYLOAD_REF_INVALID") from None
    if parsed["provider"] in ("-",) + SPECIAL_REFS:
        raise EnvelopeError("PAYLOAD_REF_INVALID")
    return {"kind": "file", **parsed}


def _check_len(text: str, limit: int) -> None:
    if limit > HARD_LIMIT:
        raise EnvelopeError("LIMIT_ABOVE_HARD_LIMIT")
    if not 1 <= len(text) <= limit:
        raise EnvelopeError("ENVELOPE_TOO_LONG")


def validate(env: dict, *, now: float | None = None, limit: int = HARD_LIMIT) -> dict:
    """Validate a decoded envelope. Returns a summary; raises EnvelopeError."""
    if type(env) is not dict:
        raise EnvelopeError("ENVELOPE_NOT_OBJECT")
    keys = set(env)
    if not set(REQUIRED) <= keys or not keys <= set(REQUIRED) | set(OPTIONAL):
        raise EnvelopeError("ENVELOPE_FIELDS")
    for k, val in env.items():
        if k in ("ia", "ttl", "n"):
            if type(val) is not int:
                raise EnvelopeError("ENVELOPE_TYPE")
        elif not isinstance(val, str):
            raise EnvelopeError("ENVELOPE_TYPE")
    if env["s"] != SCHEMA:
        raise EnvelopeError("SCHEMA")
    if not TEST_ID.fullmatch(env["t"]) or not KEY.fullmatch(env["k"]):
        raise EnvelopeError("ID_INVALID")
    if not AGENT.fullmatch(env["f"]) or not AGENT.fullmatch(env["r"]):
        raise EnvelopeError("AGENT_INVALID")
    action, status, version = env["a"], env["st"], env["v"]
    if action not in ACTIONS:
        raise EnvelopeError("ACTION_INVALID")
    is_request = status == REQUEST_STATUS
    if not is_request and status not in RESULT_STATUS:
        raise EnvelopeError("STATUS_INVALID")
    if version not in ("none", "create-only") and not VERSION.fullmatch(version):
        raise EnvelopeError("VERSION_INVALID")
    ttl_cap = MAX_WRITE_TTL if action in WRITES else MAX_TTL
    if not 1 <= env["ttl"] <= ttl_cap or env["ia"] <= 0:
        raise EnvelopeError("TTL_INVALID")
    now = time.time() if now is None else now
    if env["ia"] > now + 60:
        raise EnvelopeError("ISSUED_IN_FUTURE")
    if now >= env["ia"] + env["ttl"]:
        raise EnvelopeError("ENVELOPE_EXPIRED")
    fref = parse_file_ref(env["fr"])
    pref = parse_payload_ref(env["pr"])
    if "re" in env and not HEX64.fullmatch(env["re"]):
        raise EnvelopeError("REPLY_REF_INVALID")
    if "h" in env and not HEX64.fullmatch(env["h"]):
        raise EnvelopeError("DIGEST_INVALID")
    if not is_request and "re" not in env:
        raise EnvelopeError("RESULT_NEEDS_REPLY_REF")
    if "m" in env and len(env["m"]) > 64:
        raise EnvelopeError("MIME_INVALID")
    if "nm" in env and (not 1 <= len(env["nm"]) <= 80
                        or any(c in env["nm"] for c in "/\\\x00")
                        or env["nm"] in (".", "..")):
        raise EnvelopeError("NAME_INVALID")
    if "q" in env and len(env["q"]) > 120:
        raise EnvelopeError("QUERY_INVALID")
    if "c" in env and len(env["c"]) > 160:
        raise EnvelopeError("CURSOR_INVALID")
    if "n" in env and not 1 <= env["n"] <= 50:
        raise EnvelopeError("LIMIT_INVALID")
    if ("p" in env) != (pref["kind"] == "inline"):
        raise EnvelopeError("INLINE_PAYLOAD_MISMATCH")
    if "p" in env and sha256_text(env["p"]) != pref["sha256"]:
        raise EnvelopeError("INLINE_PAYLOAD_SHA256")

    if is_request:
        _check_request_semantics(env, action, version, fref, pref)
    for k, val in env.items():
        if isinstance(val, str) and k != "p":
            scan_forbidden(val)
    if "p" in env:
        scan_forbidden(env["p"])
    _check_len(canonical(env), limit)
    return {"action": action, "is_request": is_request, "file_ref": fref,
            "payload_ref": pref, "identity_verified": False,
            "library_authorized": False}


def _check_request_semantics(env, action, version, fref, pref):
    if action == "search":
        if fref["provider"] != "catalog" or version != "none" or pref["kind"] != "none":
            raise EnvelopeError("SEARCH_SHAPE")
    elif action == "status":
        # Status queries the ORIGINAL operation key only (no new key).
        if fref["provider"] != "status" or version != "none" or pref["kind"] != "none":
            raise EnvelopeError("STATUS_SHAPE")
    elif action == "read":
        if (fref["provider"] in ("-",) + SPECIAL_REFS or pref["kind"] != "none"
                or version in ("none", "create-only") or version != fref["version"]):
            raise EnvelopeError("READ_NEEDS_PINNED_VERSION")
    elif action == "create":
        if (version != "create-only" or "nm" not in env or "m" not in env
                or fref["provider"] != "library" or pref["kind"] == "none"):
            raise EnvelopeError("CREATE_SHAPE")
    elif action == "update":
        if (version in ("none", "create-only") or fref["provider"] != "library"
                or fref["version"] != version or "m" not in env
                or pref["kind"] == "none" or "nm" in env):
            raise EnvelopeError("UPDATE_NEEDS_ORIGINAL_VERSION")
    if action in WRITES:
        mime = env["m"]
        if pref["kind"] in ("inline", "chunked"):
            if mime not in TEXT_TYPES:
                raise EnvelopeError("TEXT_PAYLOAD_MIME")
        elif pref["kind"] == "file":
            if pref["provider"] not in ("sealed", "drive", "dropbox") or mime not in PREPARED_TYPES | TEXT_TYPES:
                raise EnvelopeError("FILE_PAYLOAD_REF")


def encode(env: dict, *, now: float | None = None, limit: int = RECOMMENDED_LIMIT) -> str:
    """Validate and return the canonical message string (default <= 800)."""
    validate(env, now=now, limit=limit)
    text = canonical(env)
    if len(text) > SERVER_LIMIT:  # defensive; HARD_LIMIT already below
        raise EnvelopeError("ENVELOPE_TOO_LONG")
    return text


def decode(message: str, *, now: float | None = None, limit: int = HARD_LIMIT) -> dict:
    if not isinstance(message, str):
        raise EnvelopeError("ENVELOPE_NOT_STRING")
    _check_len(message, limit)

    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise EnvelopeError("DUPLICATE_FIELD")
            out[k] = v
        return out
    try:
        env = json.loads(message, object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(EnvelopeError("NONFINITE")))
    except ValueError as exc:
        if isinstance(exc, EnvelopeError):
            raise
        raise EnvelopeError("ENVELOPE_JSON") from None
    if canonical(env) != message:
        raise EnvelopeError("ENVELOPE_NOT_CANONICAL")
    validate(env, now=now, limit=limit)
    return env


def request_digest(env: dict) -> str:
    """Digest binding a result to its request (excludes reply-only fields)."""
    core = {k: v for k, v in env.items() if k not in ("st", "re", "h")}
    return sha256_text(canonical(core))


def inbox_key_for(operation_key: str, kind: str, n: int = 0) -> str:
    """Inbox operation_key per message. The envelope ``k`` never changes;
    chunk/status/result messages get a derived *transport* key."""
    if kind == "req":
        return operation_key
    suffix = {"part": "-p%02d", "query": "-q%02d", "result": "-r%02d"}[kind] % n
    key = operation_key + suffix
    if not KEY.fullmatch(key):
        raise EnvelopeError("DERIVED_KEY_TOO_LONG")
    return key


# ---------------------------------------------------------------- chunking

def _render_chunk(op_key, i, total, whole, part):
    return canonical({"s": CHUNK_SCHEMA, "k": op_key, "i": i, "n": total,
                      "h": sha256_text(part), "w": whole, "x": part})


def split_text(text: str, op_key: str, *, limit: int = RECOMMENDED_LIMIT) -> tuple[str, list[str]]:
    """Split long text into dlc/2 chunk messages; returns (payload_ref, parts).

    Every rendered part (all IDs and hashes included) is <= ``limit`` chars.
    """
    if limit > HARD_LIMIT:
        raise EnvelopeError("LIMIT_ABOVE_HARD_LIMIT")
    if not isinstance(text, str) or len(text) < 2 or not KEY.fullmatch(op_key):
        raise EnvelopeError("CHUNK_INPUT")
    scan_forbidden(text)
    whole = sha256_text(text)
    parts, i = [], 0
    while i < len(text):
        # worst-case 2-digit part numbers while sizing
        j = len(text)
        while j > i and len(_render_chunk(op_key, MAX_PARTS, MAX_PARTS, whole, text[i:j])) > limit:
            over = len(_render_chunk(op_key, MAX_PARTS, MAX_PARTS, whole, text[i:j])) - limit
            j -= max(1, min(over, j - i - 1)) if j - i > 1 else 1
        if j == i:
            raise EnvelopeError("CHUNK_ROOM")
        parts.append(text[i:j])
        i = j
        if len(parts) > MAX_PARTS:
            raise EnvelopeError("CHUNK_TOO_MANY_PARTS")
    if len(parts) == 1:  # short text: inline payload is the right tool
        raise EnvelopeError("CHUNK_NOT_NEEDED_USE_INLINE")
    total = len(parts)
    msgs = [_render_chunk(op_key, n + 1, total, whole, p) for n, p in enumerate(parts)]
    assert all(len(m) <= limit for m in msgs)
    return "ck:%s:%d" % (whole, total), msgs


def parse_chunk(message: str) -> dict:
    if not isinstance(message, str) or not 1 <= len(message) <= HARD_LIMIT:
        raise EnvelopeError("CHUNK_LENGTH")
    try:
        c = json.loads(message)
    except ValueError:
        raise EnvelopeError("CHUNK_JSON") from None
    if (type(c) is not dict or set(c) != {"s", "k", "i", "n", "h", "w", "x"}
            or c["s"] != CHUNK_SCHEMA or canonical(c) != message
            or not isinstance(c["k"], str) or not KEY.fullmatch(c["k"])
            or type(c["i"]) is not int or type(c["n"]) is not int
            or not 2 <= c["n"] <= MAX_PARTS or not 1 <= c["i"] <= c["n"]
            or not isinstance(c["h"], str) or not HEX64.fullmatch(c["h"])
            or not isinstance(c["w"], str) or not HEX64.fullmatch(c["w"])
            or not isinstance(c["x"], str) or not c["x"]):
        raise EnvelopeError("CHUNK_SHAPE")
    if sha256_text(c["x"]) != c["h"]:
        raise EnvelopeError("CHUNK_PART_SHA256")
    return c


class Reassembler:
    """In-memory, per-call reassembly. Not a Store: nothing is persisted.

    ``result()`` returns the text only when every part arrived, every part
    hash and the whole-message hash match, and the TTL has not lapsed.
    Missing parts => status ``WAITING`` (or ``UNKNOWN_QUERY_ONLY`` after the
    deadline) with the exact transport keys to look up via
    ``gateway_inbox_list``. It never asks the sender to resend.
    """

    def __init__(self, payload_ref: str, op_key: str, deadline: float):
        ref = parse_payload_ref(payload_ref)
        if ref["kind"] != "chunked" or not KEY.fullmatch(op_key):
            raise EnvelopeError("REASSEMBLY_REF")
        self.whole, self.total, self.key = ref["sha256"], ref["total"], op_key
        self.deadline = deadline
        self.parts: dict[int, str] = {}

    def add(self, message: str) -> None:
        c = parse_chunk(message)
        if c["k"] != self.key or c["w"] != self.whole or c["n"] != self.total:
            raise EnvelopeError("CHUNK_FOREIGN")
        old = self.parts.get(c["i"])
        if old is not None and old != c["x"]:
            raise EnvelopeError("CHUNK_CONFLICT")
        self.parts[c["i"]] = c["x"]

    def missing(self) -> list[int]:
        return [i for i in range(1, self.total + 1) if i not in self.parts]

    def result(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        missing = self.missing()
        if missing:
            return {"status": "UNKNOWN_QUERY_ONLY" if now >= self.deadline else "WAITING",
                    "missing": missing,
                    "query_inbox_keys": [inbox_key_for(self.key, "part", i) for i in missing],
                    "resend": False}
        if now >= self.deadline:
            return {"status": "UNKNOWN_QUERY_ONLY", "missing": [], "resend": False,
                    "reason": "EXPIRED_BEFORE_PROCESSING"}
        text = "".join(self.parts[i] for i in range(1, self.total + 1))
        if sha256_text(text) != self.whole:
            raise EnvelopeError("CHUNK_WHOLE_SHA256")
        return {"status": "COMPLETE", "text": text, "sha256": self.whole}


# ------------------------------------------------- alignment to PR #61/#62

def to_pr61_request(env: dict, content: str = "") -> dict:
    """Map a validated request envelope to PR #61 deer-library-original-request-v1.
    ``content`` is the verified inline/reassembled text, or the sealed handle id."""
    summary = validate(env, now=env["ia"])
    if not summary["is_request"]:
        raise EnvelopeError("NOT_A_REQUEST")
    action, fref, pref = env["a"], summary["file_ref"], summary["payload_ref"]
    if pref["kind"] == "inline":
        content, kind = env["p"], "text"
    elif pref["kind"] == "chunked":
        if sha256_text(content) != pref["sha256"]:
            raise EnvelopeError("CONTENT_NOT_REASSEMBLED")
        kind = "text"
    elif pref["kind"] == "file":
        if pref["provider"] != "sealed":
            raise EnvelopeError("PREPARED_NEEDS_SEALED_HANDLE")
        content, kind = pref["id"], "prepared"
    else:
        content, kind = "", "none"
    target = {"search": "catalog", "status": "status"}.get(action) or fref["id"]
    return {"schema": "deer-library-original-request-v1", "source": "chatgpt-library",
            "operation": PR61_OPERATION[action], "operation_key": env["k"],
            "target_ref": target, "expected_version": env["v"],
            "name": env.get("nm", ""), "mime_type": env.get("m", ""),
            "payload_kind": kind, "content": content}


# ------------------------------------------------------ UNKNOWN discipline

class OperationJournal:
    """Caller-side, in-memory guard (not a Store). Enforces one key per write
    and QUERY_ONLY after UNKNOWN; never authorises a resend."""

    def __init__(self):
        self._by_key: dict[str, dict] = {}
        self._by_intent: dict[str, str] = {}

    @staticmethod
    def intent(env: dict) -> str:
        return sha256_text(canonical({k: env[k] for k in ("a", "fr", "v", "pr")}))

    def begin(self, env: dict) -> str:
        key, intent = env["k"], self.intent(env)
        if key in self._by_key:
            raise EnvelopeError("KEY_ALREADY_USED_QUERY_ONLY")
        prior = self._by_intent.get(intent)
        if prior is not None and self._by_key[prior]["state"] in ("sent", "UNKNOWN_QUERY_ONLY"):
            raise EnvelopeError("SAME_INTENT_PENDING_QUERY_ORIGINAL_KEY")
        self._by_key[key] = {"state": "sent", "intent": intent}
        self._by_intent[intent] = key
        return key

    def settle(self, key: str, status: str) -> None:
        if key not in self._by_key or status not in RESULT_STATUS:
            raise EnvelopeError("SETTLE_INVALID")
        self._by_key[key]["state"] = status

    def next_step(self, key: str) -> str:
        state = self._by_key.get(key, {}).get("state")
        if state in ("sent", "UNKNOWN_QUERY_ONLY", "pending"):
            return "QUERY_ORIGINAL_KEY"
        return "DONE" if state else "UNKNOWN_KEY"
