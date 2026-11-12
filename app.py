"""Preview-only port of the synthetic Redis HTTP trial."""
import hashlib
import hmac
import json
import os
import re
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

from redis import Redis
from redis.exceptions import RedisError

NAMESPACE = "xj:vercel-preview:v1"
PRINCIPAL = "synthetic-preview"
security = HTTPBearer(auto_error=False)
app = FastAPI(title="Synthetic Redis Preview", version="0.1.0",
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get('/api/deer-batch/health', include_in_schema=False)
async def fresh_batch_entry_health():
    """Non-secret installation status; no task, grant, identity or key values."""
    server = globals().get('_mcp')
    names = {tool.name for tool in await server.list_tools()} if server is not None else set()
    return {'version': 'fresh-batch-entry-20261112',
            'enabled': os.getenv('XJ_FRESH_BATCH_HOST_ENABLED') == 'true',
            'configuration_present': bool(os.getenv('XJ_FRESH_BATCH_HOST_CONFIG')),
            'ingress_configured': bool(os.getenv('XJ_FRESH_BATCH_INGRESS_KEY')),
            'batch_tools_registered': all(name in names for name in ('batch_task', 'batch_status', 'cancel_batch'))}


def authorize(credentials: HTTPAuthorizationCredentials | None = Depends(security)):
    if os.getenv("VERCEL_ENV") != "preview":
        raise HTTPException(404, "Not found")
    expected = os.getenv("XJ_PREVIEW_TOKEN_SHA256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise HTTPException(503, "Preview authorization unavailable")
    supplied = hashlib.sha256(credentials.credentials.encode()).hexdigest() if credentials else ""
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})
    return PRINCIPAL


class SyntheticEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    operation_key: str = Field(min_length=1, max_length=80, pattern=r'^[A-Za-z0-9_-]+$')
    message: str = Field(min_length=1, max_length=1024)

class EventReceipt(BaseModel):
    event_id: str
    operation_key: str
    message: str
    ttl_seconds: int
    duplicate: bool
    synthetic: bool = True

WRITE = '''
if redis.call('EXISTS',KEYS[1])==1 then
 if redis.call('HGET',KEYS[1],'digest')~=ARGV[1] or redis.call('HGET',KEYS[1],'owner')~=ARGV[5] then return {'conflict'} end
 return {'existing',redis.call('HGET',KEYS[1],'payload'),redis.call('TTL',KEYS[1])}
end
redis.call('HSET',KEYS[1],'digest',ARGV[1],'payload',ARGV[2],'operation',ARGV[3],'eventId',ARGV[4],'owner',ARGV[5])
redis.call('EXPIRE',KEYS[1],60)
return {'created',ARGV[2],redis.call('TTL',KEYS[1])}
'''
READ = '''
if redis.call('EXISTS',KEYS[1])==0 or redis.call('HGET',KEYS[1],'owner')~=ARGV[1] then return {'missing'} end
return {'found',redis.call('HGET',KEYS[1],'payload'),redis.call('TTL',KEYS[1])}
'''

def call(script, key, *args):
    url = os.getenv("XJ_PREVIEW_REDIS_URL") or os.getenv("REDIS_URL", "")
    try:
        endpoint = urlsplit(url)
        if endpoint.scheme != "rediss" or not endpoint.hostname or endpoint.query or endpoint.fragment:
            raise ValueError("Redis TLS URL required")
        with Redis.from_url(
            url, decode_responses=True, ssl_cert_reqs="required",
            ssl_check_hostname=True, socket_connect_timeout=3, socket_timeout=3,
        ) as client:
            return client.eval(script, 1, key, *args)
    except (ValueError, OSError, RedisError):
        raise HTTPException(
            503, "Redis outcome unconfirmed; query the same event, do not change operation key"
        ) from None


@app.post('/api/continuation/test-event', response_model=EventReceipt)
def write_event(event: SyntheticEvent, principal: str = Depends(authorize)):
    event_id = hashlib.sha256((NAMESPACE+'|'+principal+'|'+event.operation_key).encode()).hexdigest()
    payload = json.dumps({'event_id':event_id,'operation_key':event.operation_key,'message':event.message}, sort_keys=True, separators=(',',':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    value = call(WRITE, f'{NAMESPACE}:http:{event_id}', digest, payload, event.operation_key, event_id, principal)
    if value[0] == 'conflict':
        raise HTTPException(409, 'Operation key already used with different payload')
    return {**json.loads(value[1]),'ttl_seconds':value[2],'duplicate':value[0]=='existing','synthetic':True}

@app.get('/api/continuation/test-event/{event_id}', response_model=EventReceipt)
def read_event(event_id: str, principal: str = Depends(authorize)):
    if not re.fullmatch(r'[0-9a-f]{64}',event_id):
        raise HTTPException(422, 'Invalid event ID')
    value = call(READ, f'{NAMESPACE}:http:{event_id}', principal)
    if value[0] == 'missing':
        raise HTTPException(404, 'Event not found')
    return {**json.loads(value[1]),'ttl_seconds':value[2],'duplicate':False,'synthetic':True}


# XJ_INTERNAL_MAILBOX_FIRST_BINDING_V1: explicit approved original-service scope.
from vercel_host import build_original_service as _xj_service
from routes import install_internal_route as _xj_install
_xj_install(app, service=_xj_service(approved_bootstrap=True))

from mcp_bridge import install
_mcp = install(app, write_event, read_event, SyntheticEvent)
