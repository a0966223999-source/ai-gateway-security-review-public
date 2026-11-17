# ChatGPT Library → Deer: full-catalog authorized Reader bridge (candidate)

## Purpose and limits

This bridge is **not knowledge-card-only**. It is intended for every Library file the caller is actually authorized to discover: diagrams, images, documents, JSON, ZIP, source code, memory and other types. Original ChatGPT Library remains the source of truth. No duplicate knowledge-card store or global export is created.

**This is currently an isolated consumer contract, not a live Library connection.** ChatGPT's general Library does not provide a confirmed external user Files API to the Deer deployment. Only ChatGPT-side authorized Files capabilities or a separately approved connected source can produce its actual pages. In particular the existing Deer Auth0 OAuth token does NOT authorize reading ChatGPT Library.

## Native source and connected providers

- Native ChatGPT Library: authenticated ChatGPT-side Files list/search/read; never treat private file IDs as an external OAuth bearer.
- Provider folders shown in Library (Google Drive, Dropbox, etc.) are **not** covered by a native recursive Library list. They require their own connector pagination with independent permissions, provenance and reader callbacks.
- Files hidden by locked chats or unavailable to an account remain inaccessible. Never infer that a missing search hit means a file does not exist.
- Pagination must continue until no next_cursor; only then is a catalog snapshot COMPLETE. No partial list is a complete index.
- Index may contain all **authorized** types, but the list of filenames is still private data. Do not publish a full catalog JSON to this public repo.

## Existing original MCP integration

`mcp_bridge.install(..., library_catalog_scope_resolver=...)` is an optional startup-owned binding.
Without a trusted original resolver the two new tools are **not mounted**. No new server/Issuer/Store/Source/Worker or grant is added.
The resolver must be provided by the original authorized Gateway/Host, not by an agent, env flag, self-report, ordinary OAuth login, or remote request.

Proposed MCP tools after the original host supplies its real rights:
- `library_catalog_search(query, cursor, limit)`: paginated search/list metadata.
- `library_item_read(ref, expected_version)`: bounded authorized preview of exactly one version-pinned item.

`OriginalLibraryScope` is a **capability contract**, not a privilege or an implementation of Library access. The host must check individual agent/client as well as owner and source-level file ACL on every search/read, including current revocation. `principal()` binds Owner, not per-agent Library permission. Never let the OAuth `synthetic:events` scope itself grant Library reads.

## Authorized page schema

```json
{
  "schema": "chatgpt-library-catalog-page-v1",
  "source": "chatgpt-library",
  "owner": "oauth:<sha256>",
  "revision": "<64-character lowercase snapshot SHA256>",
  "items": [
    {
      "ref": "opaque_0001",
      "name": "diagram.png",
      "kind": "file",
      "mime_type": "image/png",
      "version": "3",
      "size_bytes": 1000,
      "modified_at": "2026-11-17T00:00:00Z"
    }
  ],
  "next_cursor": "opaque-scoped-cursor"
}
```

Catalog refs and page cursors must be opaque and authoritatively mapped to real Library file ID/version **inside trusted producer only**. Never include provider tokens, share URLs, arbitrary paths or grant sources in returned item metadata.

## Authorized item schema

```json
{
  "schema": "chatgpt-library-item-view-v1",
  "source": "chatgpt-library",
  "owner": "oauth:<sha256>",
  "revision": "<same catalog snapshot SHA256>",
  "ref": "opaque_0001",
  "version": "3",
  "mime_type": "image/png",
  "preview_kind": "image_description",
  "preview": "Authorized bounded description; no raw image bytes"
}
```

The preview_kind can be `text_excerpt`, `image_description`, `binary_manifest`, or `metadata_only`. This bridge deliberately does not expose full binary contents over the public MCP preview. Future binary transfer requires a separate owner-authorized, version-pinned media path.

## Release gates and acceptance

1. First enumerate all native Library pages with the current user's Files capability. Record item count, pagination completion, and type breakdown **privately**.
2. Read only one explicitly permitted example from image/diagram, text and ZIP categories. Verify actual file version and per-file access; redact secrets.
3. Obtain the original trusted ChatGPT-side catalog producer and existing Gateway/Host Reader adapter. Check owner, client/agent, task/context, version, TTL, revocation and current source grant.
4. Bind `library_catalog_scope_resolver` in existing Gateway startup. No model or environment variable can mint this resolver.
5. Run two-agent negative checks: authorized agent receives metadata; unauthorized agent sees uniform denial, including metadata. Revoke access and prove subsequent read denied.
6. Verify full pagination and per-file latest revision; reject snapshot changed or stale version. Confirm no accidental second Store/Issuer and no public file content logging.
7. Only after independent QA and exact Owner deployment approval promote the existing Vercel deployment and Alias. No automatic alias switch from this candidate.

## Current checkpoints (2026-11-17)

- Native Library first scan: at least 1,600 entries surfaced across pages, predominantly images; **not exhausted**.
- Another recent sorted page includes ZIP, MD, JSON, PNG, GIF, Python and other formats.
- Google Drive and Dropbox mount recursion warned unsupported by native list. Dedicated provider traversal is pending.
- Existing original short-term summary channel is not a general Library Reader and still needs its own rights. Do NOT reuse it as a universal file permission.
- Original trusted Library producer/provider: NOT_BOUND.
- Live authorization / real MCP read: NOT_RUN.
- Privacy: no private catalog entries committed to this repository.
