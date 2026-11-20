"""Exact-operation binding for the original Deer approval store."""
import hashlib
import json
import re

from approval import ApprovalError

SCOPE = "knowledge"
FIELDS = {"operation", "collection", "target_ref", "expected_version",
          "operation_key", "delta_sha256"}
CREATE_FIELDS = {"name", "mime_type"}
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", re.ASCII)
DIGEST = re.compile(r"[a-f0-9]{64}", re.ASCII)


def parse_plan(raw):
    try:
        plan = json.loads(raw)
    except (TypeError, ValueError):
        raise ApprovalError("invalid_knowledge_plan") from None
    if (type(plan) is not dict or plan.get("operation") not in ("read", "create", "append", "update")
            or set(plan) != (FIELDS | CREATE_FIELDS if plan["operation"] == "create" else FIELDS)
            or plan["collection"] not in ("shared_short_memory", "shared_reflection")
            or any(type(plan[k]) is not str or not TOKEN.fullmatch(plan[k])
                   for k in ("target_ref", "expected_version", "operation_key"))
            or type(plan["delta_sha256"]) is not str
            or (plan["delta_sha256"] != "" if plan["operation"] == "read"
                else not DIGEST.fullmatch(plan["delta_sha256"]))):
        raise ApprovalError("invalid_knowledge_plan")
    if plan["operation"] == "create":
        if (plan["expected_version"] != "create-only" or plan["target_ref"] != "kb:test"
                or type(plan["name"]) is not str or not 0 < len(plan["name"]) <= 160
                or any(c in plan["name"] for c in ("/", "\\", "..", "\n", "\r", "\x00"))
                or type(plan["mime_type"]) is not str
                or not re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", plan["mime_type"])):
            raise ApprovalError("invalid_knowledge_plan")
    return plan


class MegaKnowledgeApproval:
    """Use the original approval store, restricted to configured OAuth clients."""

    def __init__(self, env, owner_of_deployment, client_factory):
        self.env = env
        self.owner_of_deployment = owner_of_deployment
        self.client_factory = client_factory
        # Isolated test folder directly under 知識庫; creates go only there (kb:test).
        name = (env.get("XJ_MEGA_KB_APPROVAL_WRITE_FOLDER") or "_gateway_test").strip()
        bad = not name or any(c in name for c in ("/", "\\", "..", "\n", "\r", "\x00"))
        # A bad name must not crash app start-up: it just leaves no usable folder.
        self.write_folder = None if bad else name

    def member(self, owner, client_id, operation):
        if owner != self.owner_of_deployment():
            return None
        if (self.env.get("XJ_MEGA_KB_APPROVAL_V1_ENABLED") != "true"
                or self.env.get("XJ_APPROVAL_ENABLED") != "true"):
            return None
        try:
            clients = json.loads(self.env.get("XJ_MEGA_KB_APPROVAL_CLIENTS_JSON", "{}"))
            entry = clients[client_id]
            name, operations = entry["member"], entry["operations"]
            if (type(name) is str and name and type(operations) is list
                    and operation in ("read", "create") and operation in operations):
                return name
        except (KeyError, ValueError, TypeError):
            pass
        return None

    def authorize(self, owner, client_id, plan):
        return self.member(owner, client_id, plan["operation"])

    def consume(self, ticket, owner, client_id, operation, ref, version, *, request=None):
        from approval import ApprovalStore

        store = ApprovalStore(owner, self.client_factory)
        try:
            saved = json.loads(store.get(ticket)["plan"])
            saved.pop("client_fingerprint", None)
            plan = parse_plan(json.dumps(saved))
            if (plan["operation"], plan["target_ref"], plan["expected_version"]) != (operation, ref, version):
                raise ApprovalError("knowledge_scope_denied")
            delta = None
            if request is not None:
                if (plan.get("name") != request["name"] or plan.get("mime_type") != request["mime_type"]
                        or request["payload_kind"] != "text"):
                    raise ApprovalError("knowledge_scope_denied")
                delta = request["content"].encode()
            return claim_knowledge(store, ticket, owner=owner, verified_client_id=client_id,
                                   raw_plan=json.dumps(plan), delta_bytes=delta,
                                   is_authorized=lambda o, c, p: self.authorize(o, c, p) is not None)
        except (KeyError, TypeError, ValueError, AttributeError):
            raise ApprovalError("knowledge_approval_denied") from None


def client_fingerprint(owner, client_id):
    if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", client_id):
        raise ApprovalError("knowledge_client_denied")
    return hashlib.sha256(json.dumps([owner, client_id], separators=(",", ":")).encode()).hexdigest()


def bound_plan(raw, fingerprint):
    return json.dumps({**parse_plan(raw), "client_fingerprint": fingerprint}, sort_keys=True,
                      separators=(",", ":"))


def claim_knowledge(store, ticket, *, owner, verified_client_id, raw_plan, is_authorized,
                    delta_bytes=None):
    """Called by the trusted knowledge executor after resolving its held MEGA port.

    The executor must recheck revocation at the actual read/write, enforce
    expected_version, and verify the returned storage version. Ticket claim
    records admission only. It does not prove that MEGA performed an effect.
    """
    plan = parse_plan(raw_plan)
    if (plan["operation"] == "read" and delta_bytes is not None
            or plan["operation"] != "read" and (
                type(delta_bytes) is not bytes
                or hashlib.sha256(delta_bytes).hexdigest() != plan["delta_sha256"])):
        raise ApprovalError("knowledge_delta_mismatch")
    client = client_fingerprint(owner, verified_client_id)
    signed_plan = bound_plan(raw_plan, client)
    data = store.get(ticket)
    if (data.get("scope") != SCOPE or data.get("plan") != signed_plan
            or data.get("plan_sha256") != hashlib.sha256(signed_plan.encode()).hexdigest()
            or is_authorized(owner, verified_client_id, plan) is not True):
        raise ApprovalError("knowledge_approval_denied")
    outcome = store.claim(ticket, client, knowledge_plan_sha256=data["plan_sha256"])
    if outcome != "claimed_now":
        raise ApprovalError("knowledge_approval_not_claimable")
    return plan
