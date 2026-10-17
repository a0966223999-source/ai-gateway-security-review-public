"""Synthetic adversarial tests; no real authentication or secrets."""
import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/"namespaced_resolver_mock_v0.py"
s=importlib.util.spec_from_file_location("subject",p)
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)

class ContractTests(unittest.TestCase):
    def setUp(self):
        self.r=m.NamespacedResolver("instance_a")
        self.issuer=self.r.test_issuer()
        self.a=self.issuer.issue_for_test(tenant="tenant_a",scopes=frozenset({"read","revoke"}))
        self.b=self.issuer.issue_for_test(tenant="tenant_b",scopes=frozenset({"read","revoke"}))
        self.reader=self.issuer.issue_for_test(tenant="tenant_a",scopes=frozenset({"read"}))
        self.ref=self.r.issue(self.a,platform="synthetic",source_id="sk_test_Q7m9Z2",expires_at=100)["ref"]
    def test_01_read(self): self.assertEqual(self.r.resolve(self.a,ref=self.ref,now=50)["decision"],"READ_ONLY")
    def test_02_no_raw_id(self): self.assertNotIn("sk_test_Q7m9Z2",str(self.r.resolve(self.a,ref=self.ref,now=50)))
    def test_03_tenant(self): self.assertEqual(self.r.resolve(self.b,ref=self.ref,now=50)["decision"],"DENY")
    def test_04_unknown_context(self): self.assertEqual(self.r.resolve(object(),ref=self.ref,now=50)["decision"],"DENY")
    def test_05_forged_dict(self): self.assertEqual(self.r.resolve({"tenant":"tenant_a","scopes":["read"]},ref=self.ref,now=50)["decision"],"DENY")
    def test_06_expired(self): self.assertEqual(self.r.resolve(self.a,ref=self.ref,now=100)["decision"],"DENY")
    def test_07_revoke(self):
        self.r.revoke(self.a,ref=self.ref)
        self.assertEqual(self.r.resolve(self.a,ref=self.ref,now=50)["decision"],"DENY")
    def test_08_no_revoke_scope(self): self.assertEqual(self.r.revoke(self.reader,ref=self.ref)["decision"],"DENY")
    def test_09_cross_tenant_revoke(self): self.assertEqual(self.r.revoke(self.b,ref=self.ref)["decision"],"DENY")
    def test_10_replay_after_revoke(self):
        self.r.revoke(self.a,ref=self.ref)
        self.assertEqual(self.r.resolve(self.a,ref=self.ref,now=1)["decision"],"DENY")
    def test_11_namespace_separation(self):
        other=m.NamespacedResolver("instance_b",token_factory=lambda:"a"*32)
        first=m.NamespacedResolver("instance_a",token_factory=lambda:"a"*32)
        x=first.issue(first.test_issuer().issue_for_test(tenant="a",scopes=frozenset({"read"})),platform="p",source_id="x",expires_at=100)["ref"]
        y=other.issue(other.test_issuer().issue_for_test(tenant="a",scopes=frozenset({"read"})),platform="p",source_id="x",expires_at=100)["ref"]
        self.assertNotEqual(x,y)
    def test_12_same_namespace_collision(self):
        a=m.NamespacedResolver("copy",token_factory=lambda:"a"*32)
        b=m.NamespacedResolver("copy",token_factory=lambda:"a"*32)
        def issue(r):
            return r.issue(r.test_issuer().issue_for_test(tenant="a",scopes=frozenset({"read"})),platform="p",source_id="x",expires_at=100)["ref"]
        self.assertEqual(issue(a),issue(b))
    def test_13_local_collision(self):
        r=m.NamespacedResolver("instance_a",token_factory=lambda:"a"*32)
        c=r.test_issuer().issue_for_test(tenant="a",scopes=frozenset({"read"}))
        self.assertEqual(r.issue(c,platform="p",source_id="x",expires_at=100)["decision"],"ISSUED")
        self.assertEqual(r.issue(c,platform="p",source_id="y",expires_at=100)["decision"],"DENY")
    def test_14_bad_namespace(self):
        with self.assertRaises(ValueError): m.NamespacedResolver("../evil")
    def test_15_bad_token(self):
        r=m.NamespacedResolver("instance_a",token_factory=lambda:"bad")
        c=r.test_issuer().issue_for_test(tenant="a",scopes=frozenset({"read"}))
        self.assertEqual(r.issue(c,platform="p",source_id="x",expires_at=100)["decision"],"DENY")
    def test_16_missing_scope(self):
        c=self.issuer.issue_for_test(tenant="tenant_a",scopes=frozenset({"revoke"}))
        self.assertEqual(self.r.resolve(c,ref=self.ref,now=50)["decision"],"DENY")
if __name__=="__main__": unittest.main()
