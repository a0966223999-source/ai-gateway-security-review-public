"""Register the existing Deer batch tools on an existing authenticated MCP server.

This module creates no server, provider, runtime, database, grant or native child.
The trusted host resolves each call and supplies its original bound tools.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,127}$")]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]


class RequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    parent_id: Text
    operation_key: Key


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: Key
    prompt: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100_000)]
    acceptance_criteria: list[Text] | None = None


class Submit(RequestBase):
    title: Text = 'Task decomposition'
    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12000)] | None = None
    items: Annotated[list[Item], Field(min_length=1, max_length=100)] | None = None
    subagent_type: Text = 'general-purpose'
    max_live_items: PositiveInt | None = None
    max_running_items: PositiveInt | None = None

    @model_validator(mode='after')
    def require_goal_or_items(self):
        if self.goal is None and self.items is None:
            raise ValueError('GOAL_OR_CHILD_ITEMS_REQUIRED')
        if self.items is not None and len({item.key for item in self.items}) != len(self.items):
            raise ValueError('DUPLICATE_BATCH_ITEM_KEYS')
        return self


class Status(RequestBase):
    batch_id: Text
    item_offset: Annotated[int, Field(strict=True, ge=0, le=1_000_000)] | None = None
    item_limit: Annotated[int, Field(strict=True, ge=1, le=25)] = 10
    item_status: Literal["pending", "queued", "leased", "running", "succeeded", "failed", "cancelled"] | None = None
    include_result: bool = False
    include_plan: bool = False


def planning_response(request: Submit) -> dict[str, Any]:
    """Prepare this GPT's planning turn; never claim a model ran on this API."""
    import hashlib
    if request.goal is None or request.items is not None:
        raise ValueError('GOAL_ONLY_PLANNING_REQUEST_REQUIRED')
    return {
        'status': 'AWAITING_CURRENT_GPT_DECOMPOSITION',
        'goal': request.goal,
        'goal_sha256': hashlib.sha256(request.goal.encode()).hexdigest(),
        'parent_id': request.parent_id,
        'operation_key': request.operation_key,
        'saved': False,
        'automatic_dispatch': False,
        'planner': 'current_gpt_work',
        'next_action': (
            'In this same GPT turn, decompose the goal into 1..100 self-contained, '
            'independent child tasks with stable unique keys, clear prompts and acceptance_criteria. '
            'Do not execute them. Call batch_task once with the identical goal, parent_id and '
            'operation_key plus those items. This preparation did not enqueue a submission. '
            'Keep each dependency chain together in one item; this independent batch is not '
            'a dependency scheduler. Then use batch_status with include_plan=true and '
            'item_offset=0 to verify the original saved goal and children. Unknown commit '
            'outcomes must be recovered with batch_receipt using the same key.'
        ),
    }


class Cancel(RequestBase):
    batch_id: Text


@dataclass(frozen=True)
class OriginalBatchScope:
    """Host-owned authority, never constructed from model request fields.

    The resolver authenticates ctx, checks the parent/version/grant and maps
    operation_key to the ORIGINAL stable tool_call_id and bound Holder.
    is_current must check current authority; it must not be a constant fallback.
    item_model is the original BatchTaskItem class.
    """

    runtime: Any
    tools: tuple[Any, Any, Any]
    item_model: type[BaseModel]
    tool_call_id: str
    is_current: Callable[[], bool]


ScopeResolver = Callable[[Context, str, str, str], Awaitable[OriginalBatchScope]]


def _check(scope: OriginalBatchScope) -> None:
    try:
        valid = (
            type(scope) is OriginalBatchScope
            and scope.runtime is not None
            and isinstance(scope.tool_call_id, str)
            and bool(scope.tool_call_id)
            and issubclass(scope.item_model, BaseModel)
            and callable(scope.is_current)
            and scope.is_current() is True
            and len(scope.tools) == 3
            and tuple(tool.name for tool in scope.tools)
            == ("batch_task", "batch_status", "cancel_batch")
            and all(inspect.iscoroutinefunction(tool.coroutine) for tool in scope.tools)
        )
    except Exception:
        valid = False
    if not valid:
        raise ToolError("BATCH_AUTHORITY_NOT_BOUND")


def _public_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"content": value}
    update = getattr(value, "update", None)
    if not isinstance(update, dict) or not isinstance(update.get("messages"), list):
        raise ValueError("ORIGINAL_TOOL_RESULT_INVALID")
    # Preserve the original tool's error/unknown/pending wording. Do not expose
    # the runtime, provider, private source or arbitrary graph update fields.
    messages = []
    for message in update["messages"]:
        if message.name != "batch_task" or message.status not in ("success", "error"):
            raise ValueError("ORIGINAL_TOOL_RESULT_INVALID")
        messages.append({"name": message.name, "status": message.status, "content": message.content})
    if not messages:
        raise ValueError("ORIGINAL_TOOL_RESULT_INVALID")
    return {"messages": messages}


async def mount_batch_tools(server: FastMCP, *, resolve_scope: ScopeResolver, oauth_meta: dict | None = None) -> None:
    """Mount once on the existing OAuth-protected server after host composition.

    Do not mount this on an anonymous synthetic Redis endpoint. A user-supplied
    parent_id/operation_key are lookup hints, never identity or authority.
    """
    if not callable(resolve_scope):
        raise ValueError("AUTHENTICATED_BATCH_SCOPE_RESOLVER_REQUIRED")
    existing_names = {tool.name for tool in await server.list_tools()}
    if existing_names.intersection({"batch_task", "batch_status", "cancel_batch"}):
        raise ValueError("ORIGINAL_BATCH_TOOL_NAME_ALREADY_REGISTERED")

    async def invoke(ctx: Context, request: RequestBase, name: str, args: dict[str, Any]):
        action = {"batch_task": "submit", "batch_status": "read", "cancel_batch": "cancel"}[name]
        try:
            scope = await resolve_scope(ctx, request.parent_id, request.operation_key, action)
            _check(scope)
        except Exception:
            raise ToolError("BATCH_SUBMISSION_DENIED") from None
        try:
            if name == "batch_task":
                args["items"] = [scope.item_model.model_validate(item) for item in args["items"]]
                args["tool_call_id"] = scope.tool_call_id
            tool = scope.tools[("batch_task", "batch_status", "cancel_batch").index(name)]
            value = await tool.coroutine(runtime=scope.runtime, **args)
            _check(scope)
            return _public_result(value)
        except Exception:
            code = "BATCH_LOOKUP_UNAVAILABLE" if action == "read" else "BATCH_CALL_OUTCOME_UNKNOWN_QUERY_ORIGINAL_KEY"
            raise ToolError(code) from None

    @server.tool(name="batch_task", meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True,
    ))
    async def batch_task(request: Submit, ctx: Context) -> dict[str, Any]:
        """Submit independent child tasks through the original authorized Holder.

        Retain the original operation key on unknown outcomes; never blindly
        resubmit. Pending is not native dispatch, person ACK or completion.
        """
        if request.items is None:
            scope = await resolve_scope(ctx, request.parent_id, request.operation_key, 'read')
            _check(scope)
            return planning_response(request)
        if request.goal is not None:
            raise ToolError('ORIGINAL_PLAN_PERSISTENCE_NOT_BOUND')
        args = request.model_dump(exclude={"parent_id", "operation_key"}, exclude_none=True)
        return await invoke(ctx, request, "batch_task", args)

    @server.tool(name="batch_status", meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
    ))
    async def batch_status(request: Status, ctx: Context) -> dict[str, Any]:
        """Read the original Holder's stored batch progress; never dispatch work."""
        args = request.model_dump(exclude={"parent_id", "operation_key"})
        if args.pop('include_plan'):
            raise ToolError('ORIGINAL_PLAN_READ_NOT_BOUND')
        return await invoke(ctx, request, "batch_status", args)

    @server.tool(name="cancel_batch", meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True,
    ))
    async def cancel_batch(request: Cancel, ctx: Context) -> dict[str, Any]:
        """Request original Holder cancellation; a receipt does not prove native stop."""
        return await invoke(ctx, request, "cancel_batch", {"batch_id": request.batch_id})
