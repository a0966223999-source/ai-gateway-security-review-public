"""Mount original source-authorized Library create/update/status tools on Deer MCP.

Merely installing these tools DOES NOT grant Library read/write rights.
The original Gateway host must supply a trusted resolver that performs per
OAuth client/source/file/task authorization and source-native CAS.

Drive import tools are mounted only when startup supplies a drive_import
object (XJ_MEGA_KB_DRIVE_IMPORT_ENABLED, default off).
"""
import re

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from library_write_gate import LibraryWriteDenied, authorized_status, authorized_write

_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,80}\Z")
_DRIVE_TOOLS = {"library_item_import_from_drive", "library_item_update_from_drive"}


def _tool_code(exc):
    code = str(exc)
    if _CODE.fullmatch(code):
        return code
    return "ORIGINAL_LIBRARY_WRITE_UNAVAILABLE"


async def mount_library_write_tools(server, *, resolve_scope, principal, client,
                                    oauth_meta=None, drive_import=None):
    if not callable(resolve_scope) or not callable(principal) or not callable(client):
        raise ValueError("ORIGINAL_LIBRARY_WRITE_RESOLVER_REQUIRED")
    if drive_import is not None and (
            not callable(getattr(drive_import, "import_file", None))
            or not callable(getattr(drive_import, "update_file", None))):
        raise ValueError("ORIGINAL_LIBRARY_DRIVE_IMPORT_REQUIRED")
    current = {tool.name for tool in await server.list_tools()}
    taken = {"library_item_create", "library_item_update", "library_write_status"}
    if drive_import is not None:
        taken |= _DRIVE_TOOLS
    if current & taken:
        raise ValueError("ORIGINAL_LIBRARY_WRITE_TOOL_ALREADY_REGISTERED")

    @server.tool(name="library_item_create", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=False, openWorldHint=False))
    async def library_item_create(
        parent_ref: str, name: str, mime_type: str, content: str,
        operation_key: str, ctx: Context, payload_kind: str = "text",
    ) -> dict:
        """Create one new item in an already-authorized parent folder.

        Text <=512KiB; for binary/diagram data, content is a host-sealed
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

    if drive_import is None:
        return

    async def _drive(coro, owner, client_id):
        try:
            receipt = await coro
        except Exception as exc:
            raise ToolError(_tool_code(exc)) from None
        if principal() != owner or client() != client_id:
            raise ToolError("ORIGINAL_LIBRARY_WRITE_UNAVAILABLE")
        return receipt

    @server.tool(name="library_item_import_from_drive", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                             idempotentHint=True, openWorldHint=True))
    async def library_item_import_from_drive(
        parent_ref: str, name: str, drive_file_id: str, expected_sha256: str,
        expected_size: int, operation_key: str, ctx: Context,
    ) -> dict:
        """Create one MEGA 知識庫 file from an allowlisted Google Drive file id.

        The caller supplies the exact size and SHA-256. The server checks the
        file's parents against the folder allowlist, reserves operation_key,
        then downloads and uploads. Same key plus the same Drive revision and
        hash returns the committed receipt. A different input is rejected.
        If this call fails after the upload may have happened, query
        library_write_status with the same operation_key. Do not retry it.
        """
        owner, client_id = principal(), client()
        return await _drive(drive_import.import_file(
            ctx, owner, client_id, parent_ref, name, drive_file_id,
            expected_sha256, expected_size, operation_key), owner, client_id)

    @server.tool(name="library_item_update_from_drive", meta=oauth_meta,
                 annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                             idempotentHint=True, openWorldHint=True))
    async def library_item_update_from_drive(
        target_ref: str, expected_version: str, drive_file_id: str,
        expected_sha256: str, expected_size: int, operation_key: str, ctx: Context,
    ) -> dict:
        """Overwrite one MEGA file from an allowlisted Drive file at expected_version.

        The Drive file and the existing file must share an extension. Text
        library_item_update is unchanged and still refuses binaries. Same
        operation_key rules as library_item_import_from_drive. Unknown results
        stay on library_write_status.
        """
        owner, client_id = principal(), client()
        return await _drive(drive_import.update_file(
            ctx, owner, client_id, target_ref, expected_version, drive_file_id,
            expected_sha256, expected_size, operation_key), owner, client_id)
