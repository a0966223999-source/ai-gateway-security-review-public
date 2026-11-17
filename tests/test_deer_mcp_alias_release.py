"""Offline guard tests: no network, no credentials, no deployment side effects."""
import unittest
from ops.deer_mcp_alias_release import (
    ALIAS, BRANCH, PROJECT, REPO, GateError, release, rollback,
)


def deployment(name, sha, *, state="READY", project=PROJECT, branch=BRANCH):
    return {
        "id": name, "projectId": project, "readyState": state, "target": None,
        "meta": {"githubCommitSha": sha, "githubCommitRef": branch,
                 "githubOrg": REPO.split("/")[0], "githubRepo": REPO.split("/")[1]},
    }


class Fake:
    OLD_SHA = "1" * 40
    NEW_SHA = "2" * 40

    def __init__(self):
        self.head = self.NEW_SHA
        self.authorized_pr = True
        self.current = "dpl_old"
        self.objects = {"dpl_old": deployment("dpl_old", self.OLD_SHA),
                        "dpl_new": deployment("dpl_new", self.NEW_SHA)}
        self.moves = []
        self.smoke_fails = False
        self.ready = True

    def branch_head(self):
        return self.head

    def merged_pr(self, sha):
        return self.authorized_pr

    def candidates(self, sha):
        return [self.objects["dpl_new"]] if self.ready else []

    def deployment(self, deployment_id):
        return self.objects[deployment_id]

    def alias(self):
        return self.current

    def assign(self, deployment_id):
        self.moves.append(deployment_id)
        self.current = deployment_id

    def smoke(self):
        if self.smoke_fails:
            raise GateError("OAUTH_METADATA_UNAVAILABLE")


class ReleaseTests(unittest.TestCase):
    def test_valid_ready_merged_pr_moves_exact_alias(self):
        api = Fake()
        result = release(api, api.NEW_SHA)
        self.assertEqual(result["status"], "RELEASE_PASS")
        self.assertEqual(api.current, "dpl_new")
        self.assertEqual(api.moves, ["dpl_new"])

    def test_dry_run_does_not_mutate(self):
        api = Fake()
        result = release(api, api.NEW_SHA, dry_run=True)
        self.assertEqual(result["status"], "PLAN_ONLY")
        self.assertEqual(api.moves, [])

    def test_direct_push_without_merged_pr_is_denied(self):
        api = Fake()
        api.authorized_pr = False
        with self.assertRaisesRegex(GateError, "NO_MERGED_PR"):
            release(api, api.NEW_SHA)
        self.assertFalse(api.moves)

    def test_stale_branch_head_is_denied(self):
        api = Fake()
        api.head = "3" * 40
        with self.assertRaisesRegex(GateError, "STALE_GIT_HEAD"):
            release(api, api.NEW_SHA)
        self.assertFalse(api.moves)

    def test_wrong_project_old_alias_denied(self):
        api = Fake()
        api.objects["dpl_old"]["projectId"] = "prj_other"
        with self.assertRaisesRegex(GateError, "DIFFERENT_PROJECT"):
            release(api, api.NEW_SHA)
        self.assertFalse(api.moves)

    def test_not_ready_new_version_denied(self):
        api = Fake()
        api.ready = False
        with self.assertRaisesRegex(GateError, "NO_MATCHING_READY"):
            release(api, api.NEW_SHA)
        self.assertFalse(api.moves)

    def test_smoke_failure_rolls_back_to_exact_previous(self):
        api = Fake()
        api.smoke_fails = True
        with self.assertRaisesRegex(GateError, "POSTCHECK_FAILED_ROLLED_BACK"):
            release(api, api.NEW_SHA)
        self.assertEqual(api.moves, ["dpl_new", "dpl_old"])
        self.assertEqual(api.current, "dpl_old")

    def test_unknown_cas_rolls_back_nothing(self):
        api = Fake()
        with self.assertRaisesRegex(GateError, "ROLLBACK_COMPARE_FAILED"):
            rollback(api, "dpl_new", "dpl_old")
        self.assertFalse(api.moves)

    def test_manual_exact_rollback_succeeds(self):
        api = Fake()
        api.current = "dpl_new"
        result = rollback(api, "dpl_new", "dpl_old")
        self.assertEqual(result["status"], "ROLLBACK_PASS")
        self.assertEqual(api.current, "dpl_old")

    def test_malformed_target_deployment_denied(self):
        api = Fake()
        api.objects["dpl_new"]["meta"]["githubCommitRef"] = "main"
        with self.assertRaisesRegex(GateError, "NO_MATCHING_READY|DIFFERENT_BRANCH"):
            release(api, api.NEW_SHA)
        self.assertEqual(api.current, "dpl_old")


if __name__ == "__main__":
    unittest.main()
