"""One ordinary-read mode on the existing Worker and loopback machine ingress."""
import base64
import json
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from summary_read_channel import MODE, SCOPE, validate_payload, validate_view

ENDPOINT = "http://127.0.0.1:8001/api/platform-checkpoint-host-rpc"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def forward_summary_read(host, envelope):
    from fresh_batch_host import canonical, digest

    body = host.verify_request(envelope)
    validate_payload(body["payload"], body["action"])
    host.reserve(body)
    result = {"kind": MODE, "status": "SUMMARY_READ_DENIED_OR_UNCONFIRMED",
              "scope": SCOPE, "native_work_authority": False}
    request = Request(ENDPOINT, data=canonical(envelope),
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect(), ProxyHandler({})).open(request, timeout=15) as response:
            if response.url != ENDPOINT:
                raise ValueError("SUMMARY_REDIRECT_DENIED")
            raw = response.read(32769)
        if len(raw) > 32768:
            raise ValueError("SUMMARY_REPLY_SIZE")
        value = json.loads(raw)
        if (type(value) is not dict or value.get("kind") != MODE
                or value.get("scope") != SCOPE or value.get("native_work_authority") is not False):
            raise ValueError("SUMMARY_REPLY_SCOPE")
        if value.get("status") == "SUMMARY_ORIGINAL_READER_VERIFIED":
            if value.get("summary_ref") != body["payload"]["summary_ref"]:
                raise ValueError("SUMMARY_REFERENCE_CHANGED")
            validate_view(value["view"], tuple(value["task_binding"]))
            result = value
        elif value.get("status") == "SUMMARY_READ_RIGHTS_NOT_CONFIGURED":
            # Copy only known fields; a denied response cannot smuggle a body.
            result["status"] = value["status"]
    except Exception:
        pass  # Signed, content-free denial below. No alternate route or retry.
    host.check(body["action"])
    if host.verify_request(envelope) != body:
        raise ValueError("SUMMARY_REQUEST_CHANGED")
    response = {"generation": host.config["generation"], "request_id": body["request_id"],
                "request_sha256": digest(body), "result": result,
                "automatic_dispatch": False, "native_execution_verified": False}
    return {"body": response,
            "signature": base64.b64encode(host.key.sign(canonical(response))).decode()}
