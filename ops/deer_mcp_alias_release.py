#!/usr/bin/env python3
"""Guarded, preview-only Vercel alias promotion and compare-and-swap rollback.

Never creates a deployment, grant, worker or token. All mutable targets are pinned.
"""
import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

REPO = "a0966223999-source/ai-gateway-security-review-public"
BRANCH = "test/vercel-fastapi-protected-preview"
PROJECT = "prj_NcyrYFsWwdbyhRc4MtmO0BlgI24W"
TEAM = "team_xXBRUZ9jNqzlMbV4MMgpBa3Y"
ALIAS = "ai-gateway-security-review-public-git-test-ver-e7ace3-yoyo-5560.vercel.app"
RESOURCE = "https://" + ALIAS + "/mcp"


class GateError(RuntimeError):
    pass


def need(value, error):
    if not value:
        raise GateError(error)
    return value


class VercelGitHub:
    def __init__(self):
        self.vercel = need(os.getenv("XJ_VERCEL_ALIAS_TOKEN"), "MISSING_VERIFIABLE_VERCEL_TOKEN")
        self.github = need(os.getenv("GITHUB_TOKEN"), "MISSING_GITHUB_READ_TOKEN")

    def _request(self, host, path, token, method="GET", data=None):
        body = json.dumps(data).encode("utf-8") if data is not None else None
        req = Request("https://" + host + path, data=body, method=method, headers={
            "Authorization": "Bearer " + token, "Accept": "application/json",
            "Content-Type": "application/json", "User-Agent": "deer-preview-alias-guard/1"})
        try:
            with urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as e:
            code = getattr(e, "code", type(e).__name__)
            raise GateError("REMOTE_CHECK_FAILED:" + str(code)) from None

    def gh(self, path):
        return self._request("api.github.com", "/repos/" + REPO + path, self.github)

    def vc(self, path, method="GET", data=None):
        delimiter = "&" if "?" in path else "?"
        return self._request("api.vercel.com", path + delimiter + urlencode({"teamId": TEAM}),
                             self.vercel, method, data)

    def branch_head(self):
        record = self.gh("/git/ref/heads/" + BRANCH)
        return (record.get("object") or {}).get("sha")

    def merged_pr(self, sha):
        prs = self.gh("/commits/" + quote(sha, safe="") + "/pulls")
        return any(p.get("merged_at") and p.get("merge_commit_sha") == sha
                   and p.get("base", {}).get("ref") == BRANCH
                   and p.get("base", {}).get("repo", {}).get("full_name") == REPO
                   for p in prs)

    def candidates(self, sha):
        data = self.vc("/v6/deployments?" + urlencode({
            "projectId": PROJECT, "sha": sha, "limit": 40}))
        return data.get("deployments", [])

    def deployment(self, deployment_id):
        return self.vc("/v13/deployments/" + quote(deployment_id, safe=""))

    def alias(self):
        data = self.vc("/v4/aliases/" + quote(ALIAS, safe=""))
        return data.get("deploymentId") or (data.get("deployment") or {}).get("id")

    def assign(self, deployment_id):
        self.vc("/v2/deployments/" + quote(deployment_id, safe="") +
                "/aliases", "POST", {"alias": ALIAS})

    def smoke(self):
        # A public MCP resource must be accessible without a pre-shared token.
        req = Request("https://" + ALIAS + "/.well-known/oauth-protected-resource",
                      headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=15) as resp:
                if resp.status != 200:
                    raise GateError("OAUTH_METADATA_HTTP_NOT_200")
                obj = json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            raise GateError("OAUTH_METADATA_UNAVAILABLE") from None
        if obj.get("resource") != RESOURCE:
            raise GateError("OAUTH_RESOURCE_MISMATCH")


def validate_deployment(dep, sha=None):
    project_id = dep.get("projectId") or (dep.get("project") or {}).get("id")
    meta = dep.get("meta") or {}
    state = dep.get("readyState") or dep.get("state")
    if project_id != PROJECT:
        raise GateError("DIFFERENT_PROJECT")
    if meta.get("githubRepo") != REPO.rsplit("/", 1)[1] or meta.get("githubOrg") != REPO.split("/", 1)[0]:
        raise GateError("DIFFERENT_REPOSITORY")
    if meta.get("githubCommitRef") != BRANCH:
        raise GateError("DIFFERENT_BRANCH")
    if state != "READY" or dep.get("target") not in (None, "preview"):
        raise GateError("NOT_READY_PREVIEW")
    if sha and meta.get("githubCommitSha") != sha:
        raise GateError("SHA_MISMATCH")
    need(dep.get("id"), "NO_DEPLOYMENT_ID")
    return dep


def release(api, sha, *, dry_run=False, poll=False):
    need(len(sha) == 40 and all(c in "0123456789abcdef" for c in sha), "INVALID_SHA")
    if api.branch_head() != sha:
        raise GateError("STALE_GIT_HEAD")
    if not api.merged_pr(sha):
        raise GateError("NO_MERGED_PR_APPROVAL")
    target = None
    attempts = 20 if poll else 1
    for i in range(attempts):
        for item in api.candidates(sha):
            if ((item.get("meta") or {}).get("githubCommitSha") == sha
                and (item.get("meta") or {}).get("githubCommitRef") == BRANCH
                and (item.get("readyState") or item.get("state")) == "READY"):
                target = validate_deployment(api.deployment(item["id"]), sha)
                break
        if target:
            break
        if i + 1 < attempts:
            time.sleep(20)
    if not target:
        raise GateError("NO_MATCHING_READY_DEPLOYMENT")
    old_id = need(api.alias(), "ALIAS_MISSING")
    old = validate_deployment(api.deployment(old_id))
    new_id = target["id"]
    if old_id == new_id:
        return {"status": "ALREADY_CURRENT", "deployment": new_id, "sha": sha}
    # Read twice to minimize races with another release or human reassignment.
    if api.branch_head() != sha or api.alias() != old_id:
        raise GateError("STALE_ALIAS_OR_HEAD")
    if dry_run:
        return {"status": "PLAN_ONLY", "from": old_id, "to": new_id, "sha": sha}
    api.assign(new_id)
    if api.alias() != new_id:
        raise GateError("ALIAS_POST_ASSIGN_UNKNOWN_NO_BLIND_RETRY")
    try:
        api.smoke()
    except GateError as smoke_error:
        if api.alias() != new_id:
            raise GateError("POSTCHECK_FAILED_ALIAS_MOVED_UNKNOWN") from None
        api.assign(old_id)
        if api.alias() != old_id:
            raise GateError("ROLLBACK_UNKNOWN") from None
        raise GateError("POSTCHECK_FAILED_ROLLED_BACK:" + str(smoke_error)) from None
    return {"status": "RELEASE_PASS", "from": old_id, "to": new_id, "sha": sha}


def rollback(api, expected_current, prior_id):
    need(expected_current and prior_id and expected_current != prior_id, "INVALID_ROLLBACK_TARGET")
    validate_deployment(api.deployment(expected_current))
    validate_deployment(api.deployment(prior_id))
    if api.alias() != expected_current:
        raise GateError("ROLLBACK_COMPARE_FAILED")
    api.assign(prior_id)
    if api.alias() != prior_id:
        raise GateError("ROLLBACK_UNKNOWN")
    return {"status": "ROLLBACK_PASS", "from": expected_current, "to": prior_id}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "release", "rollback"])
    parser.add_argument("--sha", help="Exact merged target branch commit")
    parser.add_argument("--expected-current", help="Current alias deployment ID (rollback only)")
    parser.add_argument("--prior", help="Prior READY preview deployment ID (rollback only)")
    parser.add_argument("--poll", action="store_true")
    args = parser.parse_args()
    try:
        api = VercelGitHub()
        if args.mode == "rollback":
            result = rollback(api, args.expected_current, args.prior)
        else:
            need(args.sha, "MISSING_EXPECTED_SHA")
            result = release(api, args.sha, dry_run=args.mode == "check", poll=args.poll)
        print(json.dumps(result, sort_keys=True))
    except GateError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}))
        sys.exit(2)


if __name__ == "__main__":
    main()
