"""Mount the original host-bound short-term reader on the existing OAuth MCP.

No Source, session, grant, model, queue or second memory store is created here.
Resolvers are supplied only by trusted startup. The locator-only entry selects
one summary under the caller's original read rights, without guessing a current
Work/task. The older zero-argument entry keeps its original binding contract.
Neither a locator nor a shared account creates permission.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, Awaitable, Callable

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field


@dataclass(frozen=True)
class OriginalWorkingMemoryScope:
    owner: str
    reader: Any
    source_alias: str
    is_current: Callable[[], bool]
    # The original host owns the I/O/lifetime, including thread/loop affinity.
    invoke_original: Callable[[Any, dict], Awaitable[str]]


def _check(scope: OriginalWorkingMemoryScope, owner: str) -> None:
    try:
        valid = (
            type(scope) is OriginalWorkingMemoryScope
            and scope.owner == owner
            and isinstance(scope.source_alias, str) and bool(scope.source_alias)
            and scope.reader.name == "custom_data_reader"
            and set(scope.reader.args_schema.model_fields) == {"source_id"}
            and callable(scope.reader.invoke)
            and inspect.iscoroutinefunction(scope.invoke_original)
            and callable(scope.is_current) and scope.is_current() is True
        )
    except Exception:
        valid = False
    if not valid:
        raise ToolError("ORIGINAL_SHORT_TERM_SCOPE_NOT_BOUND")


def _public_view(raw: str) -> dict:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 16384:
        raise ToolError("ORIGINAL_SHORT_TERM_VIEW_INVALID")
    view = json.loads(raw)
    if (
        not isinstance(view, dict)
        or view.get("schema") != "task-working-view-v1"
        or view.get("revoked") is not False
        or not isinstance(view.get("summary"), dict)
        or any(not isinstance(view.get(key), str)
               or re.fullmatch(r"[a-f0-9]{64}", view[key]) is None
               for key in ("sourceRevision", "sourceDigest"))
    ):
        raise ToolError("ORIGINAL_SHORT_TERM_VIEW_INVALID")
    return {
        "status": "SHORT_TERM_MEMORY_READY",
        "memory": view["summary"],
        "source_revision": view["sourceRevision"],
        "source_digest": view["sourceDigest"],
        "scope": "original-task-short-term-context-only",
        "native_work_authority": False,
        "dispatch_allowed": False,
    }


@dataclass(frozen=True)
class OriginalSummaryReadScope:
    """Host-selected existing read capability for one opaque locator.

    task_binding uses taskRef/version/operationRef/ownerRef order. The host
    resolves it afresh, using original permissions; no grant is handed over.
    """
    summary_ref: str
    task_binding: tuple[str, str, str, str]
    original: OriginalWorkingMemoryScope


_BINDING_FIELDS = ("taskRef", "version", "operationRef", "ownerRef")


def _check_summary(scope, owner, summary_ref):
    if (type(scope) is not OriginalSummaryReadScope
            or scope.summary_ref != summary_ref
            or type(scope.task_binding) is not tuple
            or len(scope.task_binding) != 4
            or any(not isinstance(v, str) or not v for v in scope.task_binding)):
        raise ToolError("ORIGINAL_SUMMARY_SCOPE_NOT_BOUND")
    _check(scope.original, owner)


async def mount_summary_read_tool(server, *, resolve_scope, principal, oauth_meta=None):
    """Reuse the original MCP and Reader; resolve_scope(ctx, owner, locator).

    Trusted startup must supply the caller's original read authorization, not
    infer it from a locator, shared account or historical delivery/ACK receipt.
    No current-Work discovery or chat access is required by this entry.
    """
    if (resolve_scope is not None and not callable(resolve_scope)) or not callable(principal):
        raise ValueError("ORIGINAL_SUMMARY_RESOLVER_REQUIRED")
    if "read_short_term_summary" in {t.name for t in await server.list_tools()}:
        raise ValueError("ORIGINAL_SUMMARY_TOOL_ALREADY_REGISTERED")

    @server.tool(
        name="read_short_term_summary", meta=oauth_meta,
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                    idempotentHint=True, openWorldHint=False),
    )
    async def read_short_term_summary(
        summary_ref: Annotated[str, Field(
            min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9:_-]*$",
        )], ctx: Context,
    ) -> dict:
        """Read one located summary using the caller's existing read rights.

        The opaque locator is not a path, Work identity or grant. The original
        Reader checks its fixed task/source/version and current read permission.
        No task guessing, chat history, cached body or checkpoint fallback.
        """
        try:
            owner = principal()
            if resolve_scope is None:
                raise ToolError("SUMMARY_READER_NOT_BOUND")
            scope = await resolve_scope(ctx, owner, summary_ref)
            if principal() != owner:
                raise ToolError("ORIGINAL_SHORT_TERM_PRINCIPAL_CHANGED")
            from summary_read_channel import RemoteSummaryReadScope, validate_view
            if type(scope) is RemoteSummaryReadScope:
                def check_remote():
                    if (scope.owner != owner or scope.summary_ref != summary_ref
                            or not callable(scope.is_current) or scope.is_current() is not True
                            or not inspect.iscoroutinefunction(scope.read_original)
                            or principal() != owner):
                        raise ToolError("ORIGINAL_SHORT_TERM_READ_UNAVAILABLE")
                check_remote()
                raw, binding = await scope.read_original()
                check_remote()
                validate_view(raw, binding)
                result = _public_view(raw)
                check_remote()
                return {**result, "summary_ref": summary_ref}
            _check_summary(scope, owner, summary_ref)
            original = scope.original
            raw = await original.invoke_original(
                original.reader, {"source_id": original.source_alias},
            )
            _check_summary(scope, owner, summary_ref)
            result = _public_view(raw)
            view = json.loads(raw)
            binding = dict(zip(_BINDING_FIELDS, scope.task_binding, strict=True))
            if view.get("binding") != binding:
                raise ToolError("ORIGINAL_SUMMARY_TASK_CHANGED")
            _check_summary(scope, owner, summary_ref)
            if principal() != owner:
                raise ToolError("ORIGINAL_SHORT_TERM_PRINCIPAL_CHANGED")
            return {**result, "summary_ref": summary_ref}
        except Exception:
            # Uniform denial: no body, authority hints or stale success leaks.
            if resolve_scope is None:
                raise ToolError("SUMMARY_READER_NOT_BOUND") from None
            raise ToolError("ORIGINAL_SHORT_TERM_READ_UNAVAILABLE") from None


async def mount_working_memory_tool(
    server: FastMCP, *, resolve_scope, principal, oauth_meta=None,
) -> None:
    if not callable(resolve_scope) or not callable(principal):
        raise ValueError("ORIGINAL_SHORT_TERM_RESOLVER_REQUIRED")
    if "read_current_short_term_memory" in {tool.name for tool in await server.list_tools()}:
        raise ValueError("ORIGINAL_SHORT_TERM_TOOL_ALREADY_REGISTERED")

    @server.tool(
        name="read_current_short_term_memory", meta=oauth_meta,
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False,
            idempotentHint=True, openWorldHint=False,
        ),
    )
    async def read_current_short_term_memory(ctx: Context) -> dict:
        """Read this caller's original current-task short-term memory.

        No task, Source, link or grant parameter is accepted. The trusted host
        selects the original reader; memory content is not execution permission.
        """
        try:
            owner = principal()
            scope = await resolve_scope(ctx, owner)
            if principal() != owner:
                raise ToolError("ORIGINAL_SHORT_TERM_PRINCIPAL_CHANGED")
            _check(scope, owner)
            # Never call .invoke in an arbitrary thread here: use original owned I/O.
            raw = await scope.invoke_original(scope.reader, {"source_id": scope.source_alias})
            _check(scope, owner)
            result = _public_view(raw)
            _check(scope, owner)
            if principal() != owner:
                raise ToolError("ORIGINAL_SHORT_TERM_PRINCIPAL_CHANGED")
            return result
        except Exception:
            # Do not expose paths, private Source, exception text or a stale view.
            # A failed read is not permission to switch to checkpoint transport.
            raise ToolError("ORIGINAL_SHORT_TERM_READ_UNAVAILABLE") from None
