"""Ordinary summary transport, independent of summary read-right provisioning.

Uses the original enrolled machine channel. Its MAC proves transport identity,
never content permission. The host must separately supply the original Reader.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import math
import os
import re
import secrets
import time
from dataclasses import dataclass

MODE = "original-short-term-summary-read-v1"
SCOPE = "original-short-term-summary-only"
FIELDS = ("taskRef", "version", "operationRef", "ownerRef")
LOCATOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,255}\Z")


class SummaryReadDenied(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def validate_payload(payload, action):
    if (type(payload) is not dict
            or set(payload) != {"execution_mode", "summary_ref"}
            or payload["execution_mode"] != MODE or action != "read"
            or not isinstance(payload["summary_ref"], str)
            or not LOCATOR.fullmatch(payload["summary_ref"])):
        raise SummaryReadDenied("SUMMARY_REQUEST_DENIED")


def validate_view(raw, binding):
    if (not isinstance(raw, str) or len(raw.encode()) > 16384
            or type(binding) is not tuple or len(binding) != 4
            or any(not isinstance(v, str) or not v for v in binding)):
        raise SummaryReadDenied("SUMMARY_VIEW_DENIED")
    view = json.loads(raw)
    if (type(view) is not dict or view.get("schema") != "task-working-view-v1"
            or view.get("binding") != dict(zip(FIELDS, binding, strict=True))
            or view.get("revoked") is not False or type(view.get("summary")) is not dict
            or any(not isinstance(view.get(k), str)
                   or not re.fullmatch(r"[a-f0-9]{64}", view[k])
                   for k in ("sourceRevision", "sourceDigest"))):
        raise SummaryReadDenied("SUMMARY_VIEW_DENIED")
    source = {k: view[k] for k in
              ("binding", "sourceRevision", "summary", "revoked")}
    if digest(source) != view["sourceDigest"]:
        raise SummaryReadDenied("SUMMARY_DIGEST_DENIED")
    return view


@dataclass(frozen=True)
class OriginalSummaryReaderPort:
    """Startup-owned original Reader capability; NOT a grant or a new Reader."""
    owner: str
    summary_ref: str
    task_binding: tuple[str, str, str, str]
    reader: object
    source_alias: str
    is_current: object
    invoke_original: object

    def check(self, owner, summary_ref):
        if (type(self) is not OriginalSummaryReaderPort or self.owner != owner
                or self.summary_ref != summary_ref
                or type(self.task_binding) is not tuple or len(self.task_binding) != 4
                or any(not isinstance(v, str) or not v for v in self.task_binding)
                or not isinstance(self.source_alias, str) or not self.source_alias
                or getattr(self.reader, "name", None) != "custom_data_reader"
                or set(self.reader.args_schema.model_fields) != {"source_id"}
                or not callable(self.reader.invoke)
                or not inspect.iscoroutinefunction(self.invoke_original)
                or not callable(self.is_current) or self.is_current() is not True):
            raise SummaryReadDenied("ORIGINAL_SUMMARY_CAPABILITY_DENIED")


def verify_machine_request(runtime, envelope):
    # Reuse original private-file checks and the current enrolled machine pin.
    # Never open a checkpoint link, Source or another account's credential.
    from app.gateway.platform_task_source import private_directory, read_at

    if (runtime is None or runtime.account is None or not runtime.current()
            or not runtime.account.current() or type(envelope) is not dict
            or set(envelope) != {"body", "mac"} or len(canonical(envelope)) > 65536):
        raise SummaryReadDenied("SUMMARY_MACHINE_DENIED")
    body = envelope["body"]
    if (type(body) is not dict or set(body) != {
            "generation", "owner", "parent_id", "action", "operation_key",
            "payload", "request_id", "expires_at"}):
        raise SummaryReadDenied("SUMMARY_MACHINE_DENIED")
    config = runtime.account.config
    parent = runtime.account.path.parent
    if (parent / "STOP").exists() or (parent / "STOP").is_symlink():
        raise SummaryReadDenied("SUMMARY_MACHINE_DENIED")
    fd = private_directory(parent)
    try:
        key = read_at(fd, "ingress.key")
    finally:
        os.close(fd)
    now = time.time()
    expiry = body["expires_at"]
    if (len(key) != 32 or not isinstance(envelope["mac"], str)
            or not hmac.compare_digest(envelope["mac"],
                hmac.new(key, canonical(body), "sha256").hexdigest())
            or any(body[k] != config[k] for k in ("generation", "owner", "parent_id"))
            or body["action"] not in config["actions"]
            or not isinstance(body["operation_key"], str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", body["operation_key"])
            or body["request_id"] != digest([
                body["generation"], body["owner"], body["action"], body["operation_key"]])
            or type(expiry) not in (int, float) or not math.isfinite(expiry)
            or not now < expiry <= now + 120):
        raise SummaryReadDenied("SUMMARY_MACHINE_DENIED")
    validate_payload(body["payload"], body["action"])
    return body


async def read_from_original_host(runtime, envelope, *, resolve_reader):
    """Finish the channel even when rights have deliberately not been assigned."""
    body = verify_machine_request(runtime, envelope)
    if resolve_reader is None:
        return {"kind": MODE, "status": "SUMMARY_READ_RIGHTS_NOT_CONFIGURED",
                "scope": SCOPE, "native_work_authority": False}
    if not inspect.iscoroutinefunction(resolve_reader):
        raise SummaryReadDenied("ORIGINAL_READER_PROVIDER_REQUIRED")
    owner, ref = body["owner"], body["payload"]["summary_ref"]
    port = await resolve_reader(owner, ref)
    if type(port) is not OriginalSummaryReaderPort:
        raise SummaryReadDenied("ORIGINAL_READER_PROVIDER_REQUIRED")
    port.check(owner, ref)
    if verify_machine_request(runtime, envelope) != body:
        raise SummaryReadDenied("SUMMARY_MACHINE_CHANGED")
    raw = await port.invoke_original(port.reader, {"source_id": port.source_alias})
    port.check(owner, ref)
    validate_view(raw, port.task_binding)
    if verify_machine_request(runtime, envelope) != body:
        raise SummaryReadDenied("SUMMARY_MACHINE_CHANGED")
    port.check(owner, ref)
    return {"kind": MODE, "status": "SUMMARY_ORIGINAL_READER_VERIFIED",
            "scope": SCOPE, "native_work_authority": False,
            "summary_ref": ref, "task_binding": list(port.task_binding), "view": raw}


async def handle_summary_machine_request(request, envelope):
    """Dispatch on the EXISTING machine-only ingress, with separate read policy."""
    from starlette.responses import JSONResponse

    if request.cookies or request.headers.get("origin") or request.headers.get("authorization"):
        return JSONResponse({"error": "SUMMARY_MACHINE_DENIED"}, status_code=403)
    try:
        runtime = getattr(request.app.state, "platform_source_runtime", None)
        provider = getattr(request.app.state, "original_summary_reader_provider", None)
        result = await read_from_original_host(runtime, envelope, resolve_reader=provider)
        return JSONResponse(result)
    except Exception:
        # Neither denied callbacks nor transport exceptions may reveal a body.
        return JSONResponse({"error": "ORIGINAL_SHORT_TERM_READ_UNAVAILABLE"}, status_code=403)


@dataclass(frozen=True)
class RemoteSummaryReadScope:
    """Original RPC proxy; deliberately does not impersonate custom_data_reader."""
    owner: str
    summary_ref: str
    is_current: object
    read_original: object


def make_summary_scope_resolver(*, env=os.environ, connection_factory=None):
    """Bind the existing MCP to its original RPC, independently of read grants."""
    rpc = None
    if env.get("XJ_FRESH_BATCH_HOST_ENABLED") == "true" and env.get("XJ_FRESH_BATCH_HOST_CONFIG"):
        from fresh_batch_rpc import BatchRPC
        if connection_factory is None:
            from service_extension import original_redis_connection
            connection_factory = original_redis_connection
        rpc = BatchRPC(config=json.loads(env["XJ_FRESH_BATCH_HOST_CONFIG"]),
            ingress_key=bytes.fromhex(env["XJ_FRESH_BATCH_INGRESS_KEY"]),
            connection_factory=connection_factory)

    async def resolve(ctx, owner, summary_ref):
        if rpc is None or not isinstance(summary_ref, str) or not LOCATOR.fullmatch(summary_ref):
            raise SummaryReadDenied("SUMMARY_TRANSPORT_NOT_CONFIGURED")
        parent = rpc.config["parent_id"]

        def current():
            rpc.check(owner, parent)
            return True

        async def read_original():
            current()
            # Server-generated per call. Never query or consume an old read key.
            key = "summary-read-" + secrets.token_hex(24)
            response = await asyncio.to_thread(rpc.call, owner=owner, parent=parent,
                action="read", operation_key=key,
                payload={"execution_mode": MODE, "summary_ref": summary_ref},
                response_timeout=15)
            # Consume only this fresh transient reply. Historical receipt tools
            # never expose summary bodies; server-side expiry handles crashes.
            await asyncio.to_thread(rpc.discard_summary_reply, owner=owner, parent=parent,
                                    operation_key=key)
            current()
            result = response.get("result")
            if (type(result) is not dict or result.get("kind") != MODE
                    or result.get("scope") != SCOPE
                    or result.get("native_work_authority") is not False
                    or result.get("status") != "SUMMARY_ORIGINAL_READER_VERIFIED"
                    or result.get("summary_ref") != summary_ref):
                raise SummaryReadDenied("SUMMARY_READ_DENIED_OR_UNCONFIRMED")
            binding = tuple(result["task_binding"])
            validate_view(result["view"], binding)
            return result["view"], binding

        current()
        return RemoteSummaryReadScope(owner, summary_ref, current, read_original)
    return resolve
