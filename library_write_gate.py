"""Bounded original-source Library WRITE capability contract for Deer MCP.

This file cannot grant ChatGPT Library access. Only a trusted original host
can supply a current, client-specific, source-atomic writer and receipt reader.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass

REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}\Z")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}\Z")
OPKEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
CLIENT = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
MIMETYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,79}/[A-Za-z0-9][A-Za-z0-9.+-]{0,79}\Z")
SAFE_INLINE = frozenset((
    "text/plain", "text/markdown", "application/json", "text/x-python",
    "text/yaml", "application/yaml", "application/xml", "text/csv",
))
SOURCES = frozenset(("chatgpt-library", "google-drive", "dropbox", "mega-knowledge-base"))
MAX_TEXT_BYTES = 512 * 1024  # card libraries are JSON files well over 16 KiB


class LibraryWriteDenied(ValueError):
    pass


def deny():
    raise LibraryWriteDenied("ORIGINAL_LIBRARY_WRITE_UNAVAILABLE")


@dataclass(frozen=True)
class OriginalLibraryWriteScope:
    """Original host-owned exact write authorization, not an OAuth-derived grant."""
    owner: str
    client_id: str
    source: str
    operation: str
    target_ref: str
    expected_version: str
    operation_key: str
    expires_at: float
    is_current: object
    commit_original: object
    receipt_original: object


def check_scope(scope, owner, client_id, operation, target_ref, expected_version, operation_key):
    if (type(scope) is not OriginalLibraryWriteScope
            or scope.owner != owner or scope.client_id != client_id
            or not isinstance(client_id, str) or not CLIENT.fullmatch(client_id)
            or scope.source not in SOURCES
            or scope.operation != operation or scope.target_ref != target_ref
            or scope.expected_version != expected_version
            or scope.operation_key != operation_key
            or type(scope.expires_at) not in (int, float)
            or not time.time() < scope.expires_at <= time.time() + 120
            or not callable(scope.is_current) or scope.is_current() is not True
            or not inspect.iscoroutinefunction(scope.commit_original)
            or not inspect.iscoroutinefunction(scope.receipt_original)):
        deny()


def validate_request(operation, target_ref, expected_version, operation_key,
                     mime_type, content, payload_kind, name):
    if (operation not in ("create", "update")
            or not isinstance(target_ref, str) or not REF.fullmatch(target_ref)
            or not isinstance(operation_key, str) or not OPKEY.fullmatch(operation_key)
            or not isinstance(mime_type, str) or not MIMETYPE.fullmatch(mime_type)
            or payload_kind not in ("text", "prepared")
            or not isinstance(content, str)):
        deny()
    if operation == "create":
        if (expected_version != "create-only" or not isinstance(name, str)
                or not 0 < len(name) <= 160 or name in (".", "..")
                or "/" in name or "\\" in name or "\x00" in name):
            deny()
    elif name != "" or not isinstance(expected_version, str) or not VERSION.fullmatch(expected_version):
        deny()
    if payload_kind == "text":
        if mime_type not in SAFE_INLINE or len(content.encode("utf-8")) > MAX_TEXT_BYTES:
            deny()
        if mime_type == "application/json":
            try:
                json.loads(content)
            except (ValueError, TypeError):
                deny()
    elif not REF.fullmatch(content):
        # Prepared refs refer to a *source-controlled* staged binary. They must
        # be resolved under exact current owner/client/operation rights by host.
        deny()
    return {
        "operation": operation, "target_ref": target_ref,
        "expected_version": expected_version, "operation_key": operation_key,
        "mime_type": mime_type, "payload_kind": payload_kind,
        "content": content, "name": name,
    }


def request_digest(request):
    raw = json.dumps(request, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_receipt(receipt, scope, request):
    if (type(receipt) is not dict or set(receipt) != {
            "schema", "source", "owner", "client_id", "operation",
            "target_ref", "expected_version", "operation_key", "ref", "version", "status", "request_digest"}
            or receipt["schema"] != "original-library-write-receipt-v1"
            or receipt["source"] != scope.source or receipt["owner"] != scope.owner
            or receipt["client_id"] != scope.client_id
            or receipt["operation"] != scope.operation
            or receipt["target_ref"] != scope.target_ref
            or receipt["expected_version"] != scope.expected_version
            or receipt["operation_key"] != scope.operation_key
            or receipt["status"] != "committed"
            or receipt["request_digest"] != request_digest(request)
            or not isinstance(receipt["ref"], str) or not REF.fullmatch(receipt["ref"])
            or not isinstance(receipt["version"], str) or not VERSION.fullmatch(receipt["version"])):
        deny()
    # MEGA replaces the file node on overwrite (the old one becomes its previous
    # version), so an update there returns a new ref; every other source keeps it.
    if (scope.operation == "update" and receipt["ref"] != scope.target_ref
            and scope.source != "mega-knowledge-base"):
        deny()
    return receipt


async def authorized_write(resolve_scope, principal, client, context, operation,
                           target_ref, expected_version, operation_key, mime_type,
                           content, payload_kind="text", name=""):
    if not callable(resolve_scope) or not callable(principal) or not callable(client):
        deny()
    request = validate_request(operation, target_ref, expected_version, operation_key,
                               mime_type, content, payload_kind, name)
    try:
        owner, client_id = principal(), client()
        if not isinstance(owner, str) or not owner:
            deny()
        scope = await resolve_scope(context, owner, client_id, operation,
                                    target_ref, expected_version, operation_key)
        check_scope(scope, owner, client_id, operation, target_ref,
                    expected_version, operation_key)
        # commit_original is required to provide source-native create-if-absent /
        # compare-and-swap and same-operation-key idempotency; this contract
        # intentionally cannot synthesize either guarantee on its behalf.
        await scope.commit_original(request)
        check_scope(scope, owner, client_id, operation, target_ref,
                    expected_version, operation_key)
        receipt = await scope.receipt_original(operation_key)
        check_scope(scope, owner, client_id, operation, target_ref,
                    expected_version, operation_key)
        if principal() != owner or client() != client_id:
            deny()
        return validate_receipt(receipt, scope, request)
    except Exception:
        # A failed commit call might have actually written. Caller must use
        # *same* operation_key in read-only status; never blind retry.
        deny()


async def authorized_status(resolve_scope, principal, client, context, operation_key):
    if (not callable(resolve_scope) or not callable(principal) or not callable(client)
            or not isinstance(operation_key, str) or not OPKEY.fullmatch(operation_key)):
        deny()
    try:
        owner, client_id = principal(), client()
        # Dedicated same-key/status capability; caller cannot select arbitrary
        # file refs, impersonate another agent or convert status into write auth.
        scope = await resolve_scope(context, owner, client_id, "status",
                                    "status", "status-only", operation_key)
        check_scope(scope, owner, client_id, "status", "status",
                    "status-only", operation_key)
        receipt = await scope.receipt_original(operation_key)
        check_scope(scope, owner, client_id, "status", "status",
                    "status-only", operation_key)
        if principal() != owner or client() != client_id:
            deny()
        if (type(receipt) is not dict
                or receipt.get("schema") != "original-library-write-receipt-v1"
                or receipt.get("source") != scope.source
                or receipt.get("owner") != owner or receipt.get("client_id") != client_id
                or receipt.get("operation_key") != operation_key
                or receipt.get("status") not in ("committed", "pending", "denied")):
            deny()
        return {"status": receipt["status"], "operation_key": operation_key,
                "source": scope.source, "native_task_authority": False}
    except Exception:
        deny()
