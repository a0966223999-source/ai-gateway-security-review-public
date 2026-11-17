# Deer MCP | Guarded Preview Alias Release

## Exact scope
GitHub: `a0966223999-source/ai-gateway-security-review-public` branch `test/vercel-fastapi-protected-preview`.
Vercel: `team_xXBRUZ9jNqzlMbV4MMgpBa3Y`, project `prj_NcyrYFsWwdbyhRc4MtmO0BlgI24W`.
Alias: `ai-gateway-security-review-public-git-test-ver-e7ace3-yoyo-5560.vercel.app`.
Preview-only; never starts workers, grants, G0, or production promotion.

## Automatic path after a reviewed merge
1. Trigger on push to the original preview branch; run offline negative QA.
2. Job runs only when GitHub variable `XJ_MCP_ALIAS_AUTO_ENABLED=true` and approved environment `deer-mcp-alias-release` allows access.
3. Verify exact Git HEAD, merged PR, Vercel project/team, branch, SHA, and READY preview deployment.
4. Read old alias target twice and verify it is the same-project READY preview; stale or UNKNOWN -> DENY.
5. Assign the existing alias to the new deployment, then read the alias independently.
6. Check public OAuth protected-resource metadata matches the fixed Alias `/mcp` URL.
7. If metadata fails, only if Alias still points at this release, reassign old deployment and independently read back.

This is a bounded best-effort compare-and-swap, not a Vercel transactional CAS. Concurrent external edits can remain UNKNOWN.
No new issuer, model keys, OAuth policies, SQL schema, Source, routing, or second deployment system.

## One-time activation requirements (not implemented by this PR)
1. Owner sets GitHub environment `deer-mcp-alias-release` with branch restriction to preview branch. Required reviewers recommended for per-release approval.
2. Owner configures GitHub secret `XJ_VERCEL_ALIAS_TOKEN`, with only the Vercel team/project alias access needed. Never paste or log its value.
3. Independent QA validates code and protected environment; then Owner enables GitHub repository variable `XJ_MCP_ALIAS_AUTO_ENABLED=true`.
4. Merge reviewed PR into preview branch. A merge must pass GitHub and Vercel SHA checks; a direct push is not authorized by this pipeline.
5. Verify Action log `RELEASE_PASS` plus independently read Alias target. `READY` alone does not mean released.
If no token / required protection / approval source, keep flag unset; it is a NO-WRITE candidate.

## Exact rollback
Record `from` and `to` deployment IDs from the release receipt.
Use the existing approved Vercel Alias UI/connector to return Alias to `from`, or run this script with authorized Vercel token:

    python ops/deer_mcp_alias_release.py rollback --expected-current dpl_NEW --prior dpl_OLD

Rollback denies when Alias no longer equals the expected deployment, or either version is not an eligible READY preview in the original project.
Read back the Alias after any rollback. Git history, Auth0, active requests, queues, and migrations are not rolled back.
Old deployments stay available. Do not blind retry an UNKNOWN alias write.

## Limitations
GitHub Actions availability and token access are not yet proven; candidate PR does not make an always-on release worker.
Protected Vercel Preview may block public OAuth metadata and cause a safe rollback; validate this before enabling.
Offline unit tests do not prove live GPT/Grok/Cursor tool calls.
