"""Mount authorized ChatGPT Library catalog tools on the EXISTING Deer MCP server.

No new OAuth scope, Issuer, global Library token, worker or store.
Absent trusted original host resolver => neither tool is mounted.
"""
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from library_catalog_gate import LibraryReadDenied, authorized_read, authorized_search


async def mount_library_catalog_tools(server, *, resolve_scope, principal, oauth_meta=None):
    if not callable(resolve_scope) or not callable(principal):
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
            return await authorized_search(resolve_scope, principal, ctx,
                                           query, cursor, limit)
        except LibraryReadDenied:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError("ORIGINAL_LIBRARY_READ_UNAVAILABLE") from None

    @server.tool(name="library_item_read", meta=oauth_meta,
                 annotations=safe_read)
    async def library_item_read(ref: str, expected_version: str,
                                ctx: Context) -> dict:
        """Read a bounded version-pinned view of ONE already-authorized file.

        Supports typed previews only; source files and image bytes are never
        copied to the public MCP service through this contract.
        """
        try:
            return await authorized_read(resolve_scope, principal, ctx,
                                         ref, expected_version)
        except LibraryReadDenied:
            from mcp.server.fastmcp.exceptions import ToolError
            raise ToolError("ORIGINAL_LIBRARY_READ_UNAVAILABLE") from None
