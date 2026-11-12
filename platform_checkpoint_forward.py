"""Existing worker -> original Gateway loopback, retaining signed ingress bytes."""
import base64
import json
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MODE="platform-checkpoint-v1"
ENDPOINT="http://127.0.0.1:8001/api/platform-checkpoint-host-rpc"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        return None


def forward_checkpoint(host,envelope):
    # The original host checks its pin, action, owner, generation, deadline and
    # transport MAC before reserving the original durable attempt once.
    from fresh_batch_host import canonical,digest
    body=host.verify_request(envelope)
    if body["payload"].get("execution_mode")!=MODE:
        raise ValueError("CHECKPOINT_MODE_REQUIRED")
    host.reserve(body)
    request=Request(ENDPOINT,data=canonical(envelope),headers={"Content-Type":"application/json"})
    try:
        with build_opener(NoRedirect(),ProxyHandler({})).open(request,timeout=15) as response:
            if response.url!=ENDPOINT:
                raise ValueError("CHECKPOINT_REDIRECT_REJECTED")
            raw=response.read(65537)
        if len(raw)>65536:
            raise ValueError("CHECKPOINT_RESPONSE_TOO_LARGE")
        result=json.loads(raw)
        if result.get("scope")!="platform-checkpoint-only" or result.get("native_work_authority") is not False:
            raise ValueError("CHECKPOINT_RESPONSE_SCOPE")
        result=dict(result,kind=MODE,status="CHECKPOINT_RESULT_VERIFIED")
    except HTTPError as exc:
        # No arbitrary body or secrets enter the signed/public receipt.
        result={"kind":MODE,"status":"CHECKPOINT_GATEWAY_REJECTED_OR_UNKNOWN_QUERY_ONLY",
                "http_status":exc.code,"scope":"platform-checkpoint-only","native_work_authority":False}
    except Exception:
        result={"kind":MODE,"status":"CHECKPOINT_OUTCOME_UNKNOWN_QUERY_ONLY",
                "scope":"platform-checkpoint-only","native_work_authority":False}
    host.check(body["action"])
    response={"generation":host.config["generation"],"request_id":body["request_id"],
        "request_sha256":digest(body),"result":result,"automatic_dispatch":False,"native_execution_verified":False}
    return {"body":response,"signature":base64.b64encode(host.key.sign(canonical(response))).decode()}
