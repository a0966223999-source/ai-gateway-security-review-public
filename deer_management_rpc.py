"""Management tools over the EXISTING authenticated, signed host RPC.

Tool arguments contain neither identity, grant, URL, cookie nor executable.
The configured host selects the original consent and named Gateway owner.
"""
import asyncio
import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from deer_agent_management import _name, _canonical


class ManagementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_key: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    kind: Literal["list", "read", "create", "receipt"]
    name: str | None = None
    description: str = Field(default="", max_length=32768)
    soul: str = Field(default="", max_length=32768)
    create_operation_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.:-]{1,128}$")
    payload_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return _name(value) if value is not None else value

    def scoped(self):
        fields = self.model_fields_set
        if self.kind == "list":
            valid = fields <= {"operation_key", "kind"}
        elif self.kind == "read":
            valid = fields <= {"operation_key", "kind", "name"} and self.name is not None
        elif self.kind == "create":
            valid = (fields <= {"operation_key", "kind", "name", "description", "soul"}
                     and self.name is not None and "soul" in fields)
        else:
            valid = (fields <= {"operation_key", "kind", "name", "create_operation_key", "payload_sha256"}
                     and all((self.name, self.create_operation_key, self.payload_sha256)))
        if not valid:
            raise ValueError("MANAGEMENT_OPERATION_FIELDS_INVALID")
        return self


def create_payload(request):
    config = {"name": request.name, "description": request.description,
              "tool_groups": [], "skills": [], "allowed_subagents": []}
    payload = {"config": config, "soul": request.soul}
    return payload, hashlib.sha256(_canonical(payload)).hexdigest()


def install_management_rpc_tools(mcp, *, rpc, principal, oauth_meta, enabled=False):
    if enabled is not True or "agent" not in rpc.config["actions"]:
        return False

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
    async def agent_manage(request: ManagementRequest) -> dict:
        """List/read/create explicitly consented original fixed definitions; never execute.

        Scope/name/expiry are selected by the original host, not this request.
        Query an unknown transport using agent_management_receipt and SAME key.
        For a lost create response, kind=receipt checks its durable original
        create_operation_key/payload_sha256 without repeating the write.
        Versioned update and fixed-Agent stop are not bound.
        """
        request.scoped()
        owner = principal()
        try:
            result = await asyncio.to_thread(rpc.call, owner=owner,
                parent=rpc.config["parent_id"], action="agent",
                operation_key=request.operation_key,
                payload=request.model_dump(exclude={"operation_key"}, exclude_unset=True))
            if principal() != owner:
                raise ToolError("MANAGEMENT_MCP_OWNER_CHANGED_QUERY_ONLY")
            if request.kind == "create":
                _, payload_sha = create_payload(request)
                return {**result, "create_operation_key": request.operation_key,
                        "create_payload_sha256": payload_sha}
            return result
        except ToolError:
            raise
        except Exception:
            raise ToolError("MANAGEMENT_OUTCOME_UNKNOWN_QUERY_SAME_KEY") from None

    @mcp.tool(meta=oauth_meta, annotations=ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def agent_management_receipt(operation_key: str) -> dict:
        """Read the same original management RPC key; never enqueue or extend consent."""
        # Reuse the exact operation-key validation, with no effect.
        ManagementRequest(operation_key=operation_key, kind="list")
        owner = principal()
        result = await asyncio.to_thread(rpc.query, owner=owner,
            parent=rpc.config["parent_id"], action="agent", operation_key=operation_key)
        if principal() != owner:
            raise ToolError("MANAGEMENT_MCP_OWNER_CHANGED_QUERY_ONLY")
        return result
    return True
