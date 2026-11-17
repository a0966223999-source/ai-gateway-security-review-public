"""Original held Reader/Writer selection for an existing Deer MCP process.

This is an in-process capability seam, NEVER a ChatGPT Library API client or
Source minting mechanism. Missing/invalid original host binding stays DENY.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, replace

from library_catalog_gate import OriginalLibraryScope, check_scope as check_read
from library_write_gate import OriginalLibraryWriteScope, check_scope as check_write

GENERATION = re.compile(r"[a-f0-9]{64}\Z")
NATIVE_SOURCE = "chatgpt-library"


class LibraryNativeBindingDenied(ValueError):
    pass


def denied():
    raise LibraryNativeBindingDenied("ORIGINAL_LIBRARY_HOST_CAPABILITY_NOT_BOUND")


@dataclass(frozen=True)
class OriginalLibraryHostBinding:
    """Created solely by trusted original host startup, not by env, OAuth or model.

    resolve_reader and resolve_writer must return the EXISTING original source
    capabilities, already restricted to owner/client/source/action/file/version.
    The binding itself cannot manufacture a file permission.
    """
    source: str
    generation: str
    is_current: object
    resolve_reader: object
    resolve_writer: object | None = None


def make_original_library_resolvers(binding):
    if binding is None:
        return None, None
    if (type(binding) is not OriginalLibraryHostBinding
            or binding.source != NATIVE_SOURCE
            or not isinstance(binding.generation, str)
            or not GENERATION.fullmatch(binding.generation)
            or not callable(binding.is_current)
            or binding.is_current() is not True
            or not inspect.iscoroutinefunction(binding.resolve_reader)
            or (binding.resolve_writer is not None
                and not inspect.iscoroutinefunction(binding.resolve_writer))):
        denied()

    def current():
        # Changes to original host lifetime, Source or revocation must be
        # detected for every read and both sides of every write.
        try:
            return binding.is_current() is True
        except Exception:
            return False

    async def catalog_resolver(ctx, owner, client_id, operation, ref):
        if operation not in ("catalog_search", "file_read") or not current():
            denied()
        try:
            scope = await binding.resolve_reader(ctx, owner, client_id, operation, ref)
            check_read(scope, owner, client_id)
            old = scope.is_current
            result = replace(scope, is_current=lambda: current() and old() is True)
            check_read(result, owner, client_id)
            if not current():
                denied()
            return result
        except Exception:
            denied()

    async def write_resolver(ctx, owner, client_id, operation, target_ref,
                             expected_version, operation_key):
        if (binding.resolve_writer is None or not current()
                or operation not in ("create", "update", "status")):
            denied()
        try:
            scope = await binding.resolve_writer(
                ctx, owner, client_id, operation, target_ref,
                expected_version, operation_key)
            check_write(scope, owner, client_id, operation, target_ref,
                        expected_version, operation_key)
            if scope.source != NATIVE_SOURCE:
                denied()
            old = scope.is_current
            result = replace(scope, is_current=lambda: current() and old() is True)
            check_write(result, owner, client_id, operation, target_ref,
                        expected_version, operation_key)
            if not current():
                denied()
            return result
        except Exception:
            denied()

    return catalog_resolver, write_resolver if binding.resolve_writer is not None else None


def library_mount_state(read_resolver, write_resolver):
    """Diagnostic reports only registration; never claims backend is live."""
    if read_resolver is None and write_resolver is None:
        return {
            "schema": "original-library-bridge-state-v1",
            "state": "NOT_BOUND",
            "catalog_resolver_registered": False,
            "write_resolver_registered": False,
            "source_live_verified": False,
            "native_chatgpt_library_api_available": False,
            "automatic_dispatch": False,
        }
    return {
        "schema": "original-library-bridge-state-v1",
        "state": "RESOLVER_REGISTERED_NOT_LIVE_VERIFIED",
        "catalog_resolver_registered": bool(read_resolver),
        "write_resolver_registered": bool(write_resolver),
        "source_live_verified": False,
        "native_chatgpt_library_api_available": False,
        "automatic_dispatch": False,
    }
