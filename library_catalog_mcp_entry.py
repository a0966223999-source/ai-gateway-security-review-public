"""Mount authorized ChatGPT Library catalog tools on the EXISTING Deer MCP server.

No new OAuth scope, Issuer, global Library token, worker or store.
Absent trusted original host resolver => neither tool is mounted.
"""
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from library_catalog_gate import LibraryReadDenied, authorized_read, authorized_search


async def mount_library_catalog_tools(server, *, resolve_scope, principal, client, oauth_meta=None,
                                      mega_approval=None):
    if not callable(resolve_scope) or not callable(principal) or not callable(client):
        raise ValueError("ORIGINAL_LIBRARY_READER_REQUIRED")
    tool_names = {tool.name for tool in await server.list_tools()}
    if tool_names & {"library_catalog_search", "library_item_read"}:
        raise ValueError("ORIGINAL_LIBRARY_TOOL_ALREADY_REGISTERED")
    safe_read = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                idempotentHint=True, openWorldHint=False)

    @server.tool(name="library_catalog_search", meta=oauth_meta,
                 annotations=safe_read)
    async def library_catalog_search(ctx: Context, query: str = "",
                                     cursor: str = "", limit: int = 20) -> dict:
        """Search/list the original Library under THIS caller's current read rights.

        Includes text, images, diagrams and binary metadata if authorized by
        the trusted original host. Cursor is a single scoped page continuation,
        never an all-files grant or raw ChatGPT file ID.
        """
        try:
            if mega_approval is not None and not mega_approval.member(principal(), client(), "read"):
                raise LibraryReadDenied()
            return await authorized_search(resolve_scope, principal, client, ctx,
                                           query, cursor, limit)
        except LibraryReadDenied:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError("ORIGINAL_LIBRARY_READ_UNAVAILABLE") from None

    read_annotation = safe_read if mega_approval is None else ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
    @server.tool(name="library_item_read", meta=oauth_meta, annotations=read_annotation)
    async def library_item_read(ref: str, expected_version: str,
                                ctx: Context, approval_ticket: str = "") -> dict:
        """Read a bounded version-pinned view of ONE already-authorized file.

        Supports typed previews only; source files and image bytes are never
        copied to the public MCP service through this contract.
        """
        try:
            if mega_approval is not None:
                import asyncio
                from approval import ApprovalError
                try:
                    await asyncio.to_thread(mega_approval.consume, approval_ticket, principal(), client(),
                                            "read", ref, expected_version)
                except ApprovalError:
                    raise LibraryReadDenied() from None
            return await authorized_read(resolve_scope, principal, client, ctx,
                                         ref, expected_version)
        except LibraryReadDenied:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError("ORIGINAL_LIBRARY_READ_UNAVAILABLE") from None
