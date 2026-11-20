"""ChatGPT Library catalog read contract for the ORIGINAL Deer authorized Reader.

No ChatGPT credential, file enumeration or new source authority exists here.
A trusted host must provide the effective per-caller read capability.
"""
from __future__ import annotations

import inspect
import re
from dataclasses import dataclass

SCHEMA_PAGE = "chatgpt-library-catalog-page-v1"
SCHEMA_ITEM = "chatgpt-library-item-view-v1"
SOURCE = "chatgpt-library"
MEGA_SOURCE = "mega-knowledge-base"
SOURCES = frozenset((SOURCE, MEGA_SOURCE))  # each scope carries exactly one of these
REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,127}\Z")
VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9:._-]{0,127}\Z")
CLIENT_RE = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
HEX_RE = re.compile(r"[a-f0-9]{64}\Z")
PREVIEWS = frozenset(("text_excerpt", "image_description", "binary_manifest", "metadata_only"))


class LibraryReadDenied(ValueError):
    pass


def deny():
    raise LibraryReadDenied("LIBRARY_READ_UNAVAILABLE")


@dataclass(frozen=True)
class OriginalLibraryScope:
    """Host-owned original Reader authority for one caller and catalog revision.

    Search and read callbacks MUST individually enforce current read rights
    against ChatGPT/connected-provider sources. A JWT Owner alone is not a grant.
    """
    owner: str
    client_id: str
    revision: str
    is_current: object
    search_original: object
    read_original: object
    source: str = SOURCE


def check_scope(scope, owner, client_id):
    if (type(scope) is not OriginalLibraryScope or scope.owner != owner
            or scope.client_id != client_id
            or not isinstance(client_id, str) or not CLIENT_RE.fullmatch(client_id)
            or not isinstance(owner, str) or not owner
            or not isinstance(scope.revision, str) or not HEX_RE.fullmatch(scope.revision)
            or scope.source not in SOURCES
            or not callable(scope.is_current) or scope.is_current() is not True
            or not inspect.iscoroutinefunction(scope.search_original)
            or not inspect.iscoroutinefunction(scope.read_original)):
        deny()


def validate_entry(entry):
    if type(entry) is not dict or not {"ref", "name", "kind", "mime_type", "version"} <= set(entry):
        deny()
    if (not isinstance(entry["ref"], str) or not REF_RE.fullmatch(entry["ref"])
            or not isinstance(entry["version"], str) or not VERSION_RE.fullmatch(entry["version"])
            or not isinstance(entry["name"], str) or not 0 < len(entry["name"]) <= 255
            or entry["kind"] not in ("file", "folder")
            or not isinstance(entry["mime_type"], str) or len(entry["mime_type"]) > 128):
        deny()
    if "size_bytes" in entry and (type(entry["size_bytes"]) is not int
                                  or not 0 <= entry["size_bytes"] <= 2**53):
        deny()
    if "modified_at" in entry and (not isinstance(entry["modified_at"], str)
                                   or len(entry["modified_at"]) > 64):
        deny()
    # Never expose source provider credentials, direct file URLs or ACL tokens.
    if set(entry) - {"ref", "name", "kind", "mime_type", "version", "size_bytes", "modified_at"}:
        deny()
    return entry


def validate_page(page, scope, limit):
    if (type(page) is not dict
            or set(page) != {"schema", "source", "owner", "revision", "items", "next_cursor"}
            or page["schema"] != SCHEMA_PAGE or page["source"] != scope.source
            or page["owner"] != scope.owner or page["revision"] != scope.revision
            or type(page["items"]) is not list or len(page["items"]) > limit
            or len(page["items"]) > 50
            or not isinstance(page["next_cursor"], str)
            or len(page["next_cursor"]) > 1024):
        deny()
    seen = set()
    for entry in page["items"]:
        validate_entry(entry)
        if entry["ref"] in seen:
            deny()
        seen.add(entry["ref"])
    return page


def validate_view(view, scope, ref, expected_version):
    if (type(view) is not dict
            or set(view) != {"schema", "source", "owner", "revision", "ref",
                            "version", "mime_type", "preview_kind", "preview"}
            or view["schema"] != SCHEMA_ITEM or view["source"] != scope.source
            or view["owner"] != scope.owner or view["revision"] != scope.revision
            or view["ref"] != ref or view["version"] != expected_version
            or view["preview_kind"] not in PREVIEWS
            or not isinstance(view["mime_type"], str) or len(view["mime_type"]) > 128
            or not isinstance(view["preview"], str)
            or len(view["preview"].encode("utf-8")) > (
                # MEGA 知識庫 card libraries are whole JSON files (Owner 2026-11-21).
                524288 if scope.source == MEGA_SOURCE else 32768)):
        deny()
    return view


async def authorized_search(resolve_scope, principal, client, context, query="", cursor="", limit=20):
    if (not callable(resolve_scope) or not callable(principal) or not callable(client)
            or not isinstance(query, str) or len(query) > 200
            or not isinstance(cursor, str) or len(cursor) > 1024
            or type(limit) is not int or not 1 <= limit <= 50):
        deny()
    try:
        owner, client_id = principal(), client()
        scope = await resolve_scope(context, owner, client_id, "catalog_search", "")
        check_scope(scope, owner, client_id)
        page = await scope.search_original(query, cursor, limit)
        check_scope(scope, owner, client_id)
        if principal() != owner or client() != client_id:
            deny()
        return validate_page(page, scope, limit)
    except Exception:
        deny()


async def authorized_read(resolve_scope, principal, client, context, ref, expected_version):
    if (not callable(resolve_scope) or not callable(principal) or not callable(client)
            or not isinstance(ref, str) or not REF_RE.fullmatch(ref)
            or not isinstance(expected_version, str)
            or not VERSION_RE.fullmatch(expected_version)):
        deny()
    try:
        owner, client_id = principal(), client()
        scope = await resolve_scope(context, owner, client_id, "file_read", ref)
        check_scope(scope, owner, client_id)
        view = await scope.read_original(ref, expected_version)
        check_scope(scope, owner, client_id)
        if principal() != owner or client() != client_id:
            deny()
        return validate_view(view, scope, ref, expected_version)
    except Exception:
        deny()
