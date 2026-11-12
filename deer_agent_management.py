"""Opt-in MCP adapter for the original Deer custom-agent routes.

No model client, credentials, database, server, or fallback identity lives here.
Trusted startup supplies a live owner-scoped binding and its original once
fence. Until those capabilities exist, no management tools are mounted.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping


class AgentManagementDenied(ValueError):
    """An unavailable capability or rejected management operation."""


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _text(value: object, limit: int, code: str) -> str:
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise AgentManagementDenied(code)
    return value


def _name(value: object) -> str:
    name = _text(value, 80, "AGENT_NAME_INVALID")
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None:
        raise AgentManagementDenied("AGENT_NAME_INVALID")
    return name


@dataclass(frozen=True)
class OriginalAgentBinding:
    """Non-serializable host capability; fields never come from tool JSON.

    authorize must recheck original MCP principal, Gateway user/token_version,
    the explicit management grant and current STOP/revocation for each call.
    invoke uses that user's existing authenticated Gateway HTTP transport,
    refuses redirects and retains the original middleware/CSRF/API gates.
    invoke_once is the ORIGINAL durable request fence, not an in-memory cache.
    A checkpoint-only AccountLinkStore is not a management authority or fence.
    """

    mcp_owner: str
    gateway_user_id: str
    authorize: Callable[[object, str, Mapping], Awaitable[bool]]
    invoke: Callable[[str, str, dict | None], Awaitable[dict]]
    invoke_once: Callable[[str, str, str, Callable], Awaitable[dict]] | None = None
    authority_scope: str = ""


class DeerAgentManagement:
    """Forward only known operations to the original authenticated routes."""

    def __init__(self, *, resolve_binding: Callable | None = None,
                 principal: Callable | None = None):
        if resolve_binding is not None and not callable(resolve_binding):
            raise AgentManagementDenied("TRUSTED_BINDING_RESOLVER_REQUIRED")
        if resolve_binding is not None and not callable(principal):
            raise AgentManagementDenied("ORIGINAL_MCP_PRINCIPAL_REQUIRED")
        self._resolve_binding = resolve_binding
        self._principal = principal

    async def _binding(self, context: object, action: str, payload: dict):
        if self._resolve_binding is None:
            raise AgentManagementDenied("AGENT_MANAGEMENT_NOT_BOUND")
        verified_owner = self._principal()
        if inspect.isawaitable(verified_owner):
            verified_owner = await verified_owner
        _text(verified_owner, 256, "VERIFIED_MCP_OWNER_REQUIRED")
        binding = self._resolve_binding(context)
        if inspect.isawaitable(binding):
            binding = await binding
        if (type(binding) is not OriginalAgentBinding
                or binding.authority_scope != "original-deer-agent-management"
                or not callable(binding.authorize) or not callable(binding.invoke)):
            raise AgentManagementDenied("ORIGINAL_MANAGEMENT_AUTHORITY_REQUIRED")
        _text(binding.mcp_owner, 256, "VERIFIED_MCP_OWNER_REQUIRED")
        if binding.mcp_owner != verified_owner:
            raise AgentManagementDenied("MCP_MANAGEMENT_OWNER_MISMATCH")
        user_id = _text(binding.gateway_user_id, 256, "GATEWAY_USER_REQUIRED")
        if user_id in {"default", "internal", "system"}:
            raise AgentManagementDenied("NAMED_GATEWAY_USER_REQUIRED")
        # A truthy object, coroutine returned in error, or numeric 1 is not ACK.
        if await binding.authorize(context, action, _freeze(payload)) is not True:
            raise AgentManagementDenied("AGENT_MANAGEMENT_AUTHORITY_DENIED")
        return binding

    async def list_agents(self, context: object) -> dict:
        binding = await self._binding(context, "list", {})
        return await binding.invoke("GET", "/api/agents", None)

    async def read_agent(self, context: object, name: str) -> dict:
        name = _name(name)
        binding = await self._binding(context, "read", {"name": name})
        return await binding.invoke("GET", "/api/agents/" + name, None)

    async def create_agent(self, context: object, request: dict) -> dict:
        if type(request) is not dict:
            raise AgentManagementDenied("AGENT_REQUEST_OBJECT_REQUIRED")
        # Snapshot before any await: caller mutation cannot change authorized I/O.
        if len(_canonical(request)) > 65536:
            raise AgentManagementDenied("AGENT_REQUEST_TOO_LARGE")
        request = json.loads(_canonical(request))
        if (set(request) - {"operation_key", "name", "description", "soul",
                            "tool_groups", "skills", "allowed_subagents"}
                or not {"operation_key", "name", "soul"} <= set(request)):
            raise AgentManagementDenied("AGENT_REQUEST_FIELDS_INVALID")
        key = _text(request.pop("operation_key"), 128, "OPERATION_KEY_REQUIRED")
        if re.fullmatch(r"[A-Za-z0-9_.:-]+", key) is None:
            raise AgentManagementDenied("OPERATION_KEY_INVALID")
        request["name"] = _name(request["name"])
        for field in ("description", "soul"):
            if field in request and (not isinstance(request[field], str)
                                    or len(request[field].encode("utf-8")) > 32768):
                raise AgentManagementDenied("AGENT_TEXT_INVALID")
        for field in ("tool_groups", "skills", "allowed_subagents"):
            values = request.setdefault(field, [])
            if type(values) is not list or len(values) > 100:
                raise AgentManagementDenied("EXPLICIT_CAPABILITY_LIST_REQUIRED")
            for value in values:
                _text(value, 128, "CAPABILITY_NAME_INVALID")
        binding = await self._binding(context, "create", request)
        if not callable(binding.invoke_once):
            raise AgentManagementDenied("ORIGINAL_MANAGEMENT_ONCE_FENCE_NOT_BOUND")
        payload_digest = hashlib.sha256(_canonical(request)).hexdigest()
        attempted = False

        async def perform():
            nonlocal attempted
            if attempted:
                raise AgentManagementDenied("MANAGEMENT_EFFECT_ALREADY_ATTEMPTED")
            attempted = True
            # Queue/once-fence delay must not retain a revoked authorization.
            current = await self._binding(context, "create", request)
            if (current.mcp_owner != binding.mcp_owner
                    or current.gateway_user_id != binding.gateway_user_id):
                raise AgentManagementDenied("MANAGEMENT_OWNER_CHANGED")
            result = await current.invoke("POST", "/api/agents", request)
            if type(result) is not dict or result.get("name") != request["name"]:
                raise AgentManagementDenied("ORIGINAL_AGENT_RESULT_MISMATCH_QUERY_ONLY")
            return {"status": "REGISTRY_SAVED_READBACK_REQUIRED", "agent": result,
                    "execution_started": False, "stop_verified": False}

        # UNKNOWN exceptions propagate. Never retry, query another key, or
        # mistake a name-exists 409 for this request's successful creation.
        return await binding.invoke_once("create", key, payload_digest, perform)

    async def update_agent(self, context: object, request: dict) -> dict:
        # Original AgentStore.update is an upsert without an expected-version
        # precondition. A read/hash then PUT would not be atomic CAS.
        raise AgentManagementDenied("ORIGINAL_AGENT_CAS_NOT_BOUND")

    async def stop_agent(self, context: object, name: str) -> dict:
        # DELETE removes definitions AND colocated memory; it is not a stop.
        raise AgentManagementDenied("ORIGINAL_AGENT_STOP_NOT_BOUND")


def install_agent_management_tools(mcp, *, resolve_binding=None,
                                   principal=None, oauth_meta=None) -> bool:
    """Mount in the existing MCP only after trusted startup has real ports.

    This does not switch on agents_api, amend OAuth scopes or create a grant.
    Missing live binding remains default-off rather than publishing a fake tool.
    """
    if resolve_binding is None:
        return False
    if getattr(mcp, "_deer_agent_management_mounted", False):
        raise AgentManagementDenied("AGENT_MANAGEMENT_ALREADY_MOUNTED")
    service = DeerAgentManagement(resolve_binding=resolve_binding, principal=principal)
    # The module is imported only when the existing MCP SDK is present.
    from mcp.server.fastmcp import Context
    from mcp.types import ToolAnnotations

    async def agent_list(ctx: Context) -> dict:
        """List the authenticated owner's original Deer fixed Agents."""
        return await service.list_agents(ctx)

    async def agent_read(name: str, ctx: Context) -> dict:
        """Read one original fixed Agent; does not start it."""
        return await service.read_agent(ctx, name)

    async def agent_create(request: dict, ctx: Context) -> dict:
        """Create an original fixed Agent definition; does not run a model."""
        return await service.create_agent(ctx, request)

    # FastMCP inspects annotations. Resolve the local Context before registering.
    for function in (agent_list, agent_read, agent_create):
        function.__annotations__["ctx"] = Context
        mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
            readOnlyHint=function is not agent_create, destructiveHint=False,
            idempotentHint=True, openWorldHint=False))(function)
    mcp._deer_agent_management_mounted = True
    return True
