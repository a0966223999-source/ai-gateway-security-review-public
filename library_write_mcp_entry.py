"""Mount original source-authorized Library create/update/status tools on Deer MCP.

Merely installing these tools DOES NOT grant Library read/write rights.
The original Gateway host must supply a trusted resolver that performs per
OAuth client/source/file/task authorization and source-native CAS.
"""
from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from library_write_gate import LibraryWriteDenied, authorized_status, authorized_write


async def mount_library_write_tools(server, *, resolve_scope, principal, client,
                                    oauth_meta=None):
    if not callable(resolve_scope) or not callable(principal) or not callable(client):
        raise ValueError("ORIGINAL_LIBRARY_WRITE_RESOLVER_REQUIRED")
    current = {tool.name for tool in await server.list_tools()}
    if current & {"library_item_create", "library_item_update", "library_write_status"}:
        raise ValueError("ORIGINAL_LIBRARY_WRITE_TOOL_ALREADY_REGISTERED")

    @server.tool(name="library_item_create", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=False, openWorldHint=False))
    async def library_item_create(
        parent_ref: str, name: str, mime_type: str, content: str,
        operation_key: str, ctx: Context, payload_kind: str = "text",
    ) -> dict:
        """Create one new item in an already-authorized parent folder.

        Text <=16KiB; for binary/diagram data, content is a host-sealed
        prepared reference, never a raw API credential or server-side file path.
        Every call must have fresh exact-task approval, source-native
        create-if-absent and idempotent same-operation-key write.
        """
        try:
            return await authorized_write(
                resolve_scope, principal, client, ctx, "create", parent_ref,
                "create-only", operation_key, mime_type, content, payload_kind, name)
        except LibraryWriteDenied:
            raise ToolError("ORIGINAL_LIBRARY_WRITE_UNAVAILABLE") from None

    @server.tool(name="library_item_update", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                             idempotentHint=False, openWorldHint=False))
    async def library_item_update(
        ref: str, expected_version: str, mime_type: str, content: str,
        operation_key: str, ctx: Context, payload_kind: str = "text",
    ) -> dict:
        """Replace exactly one already-authorized file at expected version.

        Caller supplies exact version and stable operation key. Original
        host must enforce atomic CAS, consent, revocation and write readback.
        """
        try:
            return await authorized_write(
                resolve_scope, principal, client, ctx, "update", ref,
                expected_version, operation_key, mime_type, content, payload_kind)
        except LibraryWriteDenied:
            raise ToolError("ORIGINAL_LIBRARY_WRITE_UNAVAILABLE") from None

    @server.tool(name="library_write_status", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=False))
    async def library_write_status(operation_key: str, ctx: Context) -> dict:
        """Read the original operation's status, never resubmit unknown writes."""
        try:
            return await authorized_status(resolve_scope, principal, client,
                                           ctx, operation_key)
        except LibraryWriteDenied:
            raise ToolError("ORIGINAL_LIBRARY_WRITE_UNAVAILABLE") from None
