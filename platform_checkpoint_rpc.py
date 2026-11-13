"""Checkpoint tools on the original OAuth MCP and existing signed host channel.

No additional Redis, mailbox, subscription, key or provider is installed.
The transport's batch execute/read capability grants no checkpoint authority:
the original Gateway must validate its separate live session-approved link.
"""
import asyncio
import json
import os
from typing import Annotated, Literal

from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

TextKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
Thread = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
MODE = "platform-checkpoint-v1"


class Bound(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    thread_id: Thread
    link_id: Digest
    operation_key: TextKey

class Create(Bound):
    checkpoint: dict
    ttl_seconds: Annotated[int,Field(strict=True,ge=1,le=600)] = 600

class Read(Bound):
    source_key: Digest

class Query(Bound):
    source_operation_key: TextKey

class Save(Read):
    expected_revision: Digest
    checkpoint: dict

class Receipt(BaseModel):
    model_config = ConfigDict(extra="forbid",strict=True)
    operation_key: TextKey
    operation_kind: Literal["read","execute"]


def install_checkpoint_rpc_tools(mcp, *, principal, oauth_meta, env=os.environ, connection_factory=None):
    if env.get("XJ_FRESH_BATCH_HOST_ENABLED") != "true" or not env.get("XJ_FRESH_BATCH_HOST_CONFIG"):
        return {"enabled":False,"tools":[]}
    from fresh_batch_rpc import BatchRPC
    if connection_factory is None:
        from service_extension import original_redis_connection
        connection_factory = original_redis_connection
    rpc = BatchRPC(config=json.loads(env["XJ_FRESH_BATCH_HOST_CONFIG"]),
        ingress_key=bytes.fromhex(env["XJ_FRESH_BATCH_INGRESS_KEY"]),
        connection_factory=connection_factory)

    def public(result, *, historical=False):
        if result.get("status") in {"PENDING_OR_UNKNOWN_QUERY_SAME_KEY", "NOT_FOUND_QUERY_ONLY"}:
            return {k:result[k] for k in ("request_id","status")}
        value = result.get("result")
        if not isinstance(value,dict) or value.get("kind") != MODE:
            raise ToolError("CHECKPOINT_REPLY_SCOPE_INVALID_QUERY_ONLY")
        if historical:
            # Retained outcome is an audit receipt, never renewed link/read authority.
            # Do not disclose checkpoint content after a cached/receipt-only lookup.
            return {"request_id":result["request_id"], "historical_only":True, "authority":False,
                    **{k:value[k] for k in ("kind","status","http_status","source_key","revision") if k in value}}
        return {"request_id":result["request_id"], **value}

    async def invoke(action, request):
        owner=principal()
        transport="read" if action in {"read","query","link_status"} else "execute"
        payload=request.model_dump(exclude={"operation_key"})
        payload.update(execution_mode=MODE,checkpoint_action=action)
        try:
            prior=await asyncio.to_thread(rpc.query,owner=owner,parent=rpc.config["parent_id"],
                operation_key=request.operation_key,action=transport)
            if principal()!=owner:
                raise ToolError("CHECKPOINT_PRINCIPAL_CHANGED")
            if prior.get("status")!="NOT_FOUND_QUERY_ONLY":
                return public(prior,historical=True)
            result=await asyncio.to_thread(rpc.call,owner=owner,parent=rpc.config["parent_id"],
                action=transport,operation_key=request.operation_key,payload=payload)
            if principal()!=owner:
                raise ToolError("CHECKPOINT_PRINCIPAL_CHANGED")
            return public(result)
        except ToolError:
            raise
        except Exception:
            raise ToolError("CHECKPOINT_OUTCOME_UNCONFIRMED_QUERY_ORIGINAL_ONLY") from None

    read_hint=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    write_hint=ToolAnnotations(readOnlyHint=False,destructiveHint=False,idempotentHint=False,openWorldHint=False)

    @mcp.tool(meta=oauth_meta,annotations=read_hint)
    async def gateway_checkpoint_link_status(request: Bound) -> dict:
        """Check the exact original session-approved link; not a Work/caller grant."""
        return await invoke("link_status",request)

    @mcp.tool(meta=oauth_meta,annotations=write_hint)
    async def gateway_checkpoint_create(request: Create) -> dict:
        """Save one bounded platform checkpoint under a live link; UNKNOWN queries only."""
        return await invoke("create",request)

    @mcp.tool(meta=oauth_meta,annotations=read_hint)
    async def gateway_checkpoint_read(request: Read) -> dict:
        """Read one original checkpoint with current account, link and user checks."""
        return await invoke("read",request)

    @mcp.tool(meta=oauth_meta,annotations=read_hint)
    async def gateway_checkpoint_query(request: Query) -> dict:
        """Query the original checkpoint operation; never create or resend it."""
        return await invoke("query",request)

    @mcp.tool(meta=oauth_meta,annotations=write_hint)
    async def gateway_checkpoint_save(request: Save) -> dict:
        """CAS-save a checkpoint under its original link, with no dispatch/ACK authority."""
        return await invoke("save",request)

    @mcp.tool(meta=oauth_meta,annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=False,openWorldHint=False))
    async def gateway_checkpoint_revoke(request: Read) -> dict:
        """Revoke one checkpoint Source; this does not stop any native Work."""
        return await invoke("revoke",request)

    @mcp.tool(meta=oauth_meta,annotations=read_hint)
    async def gateway_checkpoint_receipt(request: Receipt) -> dict:
        """Read the same signed checkpoint request outcome without enqueue or replay."""
        owner=principal()
        result=await asyncio.to_thread(rpc.query,owner=owner,parent=rpc.config["parent_id"],
            operation_key=request.operation_key,action=request.operation_kind)
        if principal()!=owner:
            raise ToolError("CHECKPOINT_PRINCIPAL_CHANGED")
        return public(result,historical=True)
    return {"enabled":True,"scope":"platform-checkpoint-only"}
