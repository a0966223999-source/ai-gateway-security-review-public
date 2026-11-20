"""AI approval tickets: any AI proposes, only the Owner's passkey approves. Default off.

Flow
  1. An AI calls approval_request(requester, scope, title, plan). Deer stores a
     pending ticket (30 min), pushes the Owner's phone and posts a notice with a
     short link /api/approval/open/<ticket> in the chat room.
  2. The link redirects to the existing passkey approval page (same Owner key as
     the Launcher) with the exact text to sign and the plan. The Owner checks the
     plan and approves with a fingerprint; the page POSTs the WebAuthn assertion
     to /api/approval/submit.
  3. Deer verifies the assertion (same checks as xjboot.py verify_assertion) and
     moves the ticket pending -> approved, then posts "@requester approved".
  4. The executor calls approval_claim(ticket) once (approved -> claimed) and
     gets the stored plan, then approval_report(ticket, result).

Signed text (prefix differs from the Launcher's, so neither accepts the other):
  xj-ai-approval v1
  ticket=<32 hex>
  requester=<member name, display only>
  scope=<code_pr|deploy|hub_write|other|knowledge>
  title=<one line, <=80 chars>
  plan_sha256=<64 hex of the UTF-8 plan>
  expires_unix=<10 digits>

Config (all Vercel env):
  XJ_APPROVAL_ENABLED=true
  XJ_APPROVAL_OWNER_KEY   JSON trusted-key entry {label,rp_id,origin,credential_id,alg,public_key_spki}
  XJ_APPROVAL_PAGE_URL    approval page, https, same origin as the key
  XJ_APPROVAL_NOTIFY_URL  optional ntfy topic URL for the Owner's phone (secret)
  XJ_APPROVAL_PUBLIC_BASE optional https base for links (default XJ_MCP_PUBLIC_HOST)
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

TTL_PENDING = 30 * 60
TTL_APPROVED_CLAIM = 24 * 3600
KEEP_SECONDS = 7 * 86400
MAX_PENDING = 5
MAX_PER_DAY = 20
MAX_PLAN = 4000
MAX_TITLE = 80
MAX_RESULT = 2000
SCOPES = ('code_pr', 'deploy', 'hub_write', 'other', 'knowledge')
NAMESPACE = 'xj:approval:v1'
SPEAKER = 'Approval'
FLAG_UP, FLAG_UV = 0x01, 0x04
TICKET = re.compile(r'^[0-9a-f]{32}$')
TEXT_RE = re.compile(
    r'^xj-ai-approval v1\nticket=([0-9a-f]{32})\nrequester=([^\n]{1,40})\n'
    r'scope=(code_pr|deploy|hub_write|other|knowledge)\ntitle=([^\n]{1,80})\n'
    r'plan_sha256=([0-9a-f]{64})\nexpires_unix=([0-9]{10})\n$')
_CONTROL = re.compile(r'[\x00-\x1f\x7f\u2028\u2029]+')
clock = time.time  # replaceable in tests


class ApprovalError(Exception):
    pass


def b64u_enc(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def b64u_dec(text):
    if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9_-]*', text):
        raise ApprovalError('bad_base64')
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def one_line(value, limit):
    text = _CONTROL.sub(' ', value if isinstance(value, str) else '').strip()
    text = re.sub(r'\s{2,}', ' ', text)
    if not text:
        raise ApprovalError('empty_field')
    return text[:limit]


def approval_text(ticket, requester, scope, title, plan_sha256, expires):
    return (f'xj-ai-approval v1\nticket={ticket}\nrequester={requester}\nscope={scope}\n'
            f'title={title}\nplan_sha256={plan_sha256}\nexpires_unix={expires}\n')


class ApprovalConfig:
    def __init__(self, env=None):
        env = os.environ if env is None else env
        self.env = env
        self.enabled = env.get('XJ_APPROVAL_ENABLED') == 'true'
        self.key = None
        self.page_url = env.get('XJ_APPROVAL_PAGE_URL', '')
        self.notify_url = env.get('XJ_APPROVAL_NOTIFY_URL', '')
        base = env.get('XJ_APPROVAL_PUBLIC_BASE') or env.get('XJ_MCP_PUBLIC_HOST', '')
        if base and not base.startswith('https://'):
            base = 'https://' + base
        self.public_base = base.rstrip('/')
        if not self.enabled:
            return
        try:
            key = json.loads(env.get('XJ_APPROVAL_OWNER_KEY', ''))
        except ValueError:
            raise ApprovalError('invalid_config_XJ_APPROVAL_OWNER_KEY') from None
        need = ('rp_id', 'origin', 'credential_id', 'public_key_spki')
        if not isinstance(key, dict) or not all(isinstance(key.get(k), str) and key[k] for k in need):
            raise ApprovalError('invalid_config_XJ_APPROVAL_OWNER_KEY')
        if key.get('alg', -7) != -7:
            raise ApprovalError('invalid_config_XJ_APPROVAL_OWNER_KEY')
        page = urlsplit(self.page_url)
        if page.scheme != 'https' or f'https://{page.hostname}' != key['origin'] or page.hostname != key['rp_id']:
            raise ApprovalError('invalid_config_XJ_APPROVAL_PAGE_URL')
        if self.notify_url and urlsplit(self.notify_url).scheme != 'https':
            raise ApprovalError('invalid_config_XJ_APPROVAL_NOTIFY_URL')
        if not self.public_base.startswith('https://'):
            raise ApprovalError('invalid_config_XJ_APPROVAL_PUBLIC_BASE')
        self.key = key


def verify_assertion(key, text, payload):
    """Raise ApprovalError unless the Owner's passkey signed exactly `text`."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if not isinstance(payload, dict) or payload.get('v') != 1:
        raise ApprovalError('bad_payload')
    if b64u_dec(payload.get('approval', '')) != text.encode():
        raise ApprovalError('approval_text_mismatch')
    if payload.get('credential_id') != key['credential_id']:
        raise ApprovalError('unknown_key')
    cdj_raw = b64u_dec(payload.get('client_data_json', ''))
    try:
        cdj = json.loads(cdj_raw)
    except ValueError:
        raise ApprovalError('client_data_json') from None
    if not isinstance(cdj, dict) or cdj.get('type') != 'webauthn.get':
        raise ApprovalError('client_data_type')
    if cdj.get('challenge') != b64u_enc(hashlib.sha256(text.encode()).digest()):
        raise ApprovalError('challenge_mismatch')
    if cdj.get('origin') != key['origin']:
        raise ApprovalError('origin_mismatch')
    if cdj.get('crossOrigin') is True:
        raise ApprovalError('cross_origin')
    auth = b64u_dec(payload.get('authenticator_data', ''))
    if len(auth) < 37:
        raise ApprovalError('auth_data_short')
    if auth[:32] != hashlib.sha256(key['rp_id'].encode()).digest():
        raise ApprovalError('rp_id_mismatch')
    if not auth[32] & FLAG_UP:
        raise ApprovalError('user_not_present')
    if not auth[32] & FLAG_UV:
        raise ApprovalError('user_not_verified')
    pub = serialization.load_der_public_key(b64u_dec(key['public_key_spki']))
    try:
        pub.verify(b64u_dec(payload.get('signature', '')),
                   auth + hashlib.sha256(cdj_raw).digest(), ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        raise ApprovalError('bad_signature') from None


# Atomic state changes. KEYS[1]=ticket hash. ARGV[1]=now.
APPROVE = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing' end
local st = redis.call('HGET', KEYS[1], 'state')
if st ~= 'pending' then return st end
if tonumber(redis.call('HGET', KEYS[1], 'expires')) < tonumber(ARGV[1]) then return 'expired' end
redis.call('HSET', KEYS[1], 'state', 'approved', 'approved_at', ARGV[1])
return 'approved_now'
"""
CLAIM = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 'missing' end
local st = redis.call('HGET', KEYS[1], 'state')
if st ~= 'approved' then return st end
local scope = redis.call('HGET', KEYS[1], 'scope')
if scope == 'knowledge' then
  if ARGV[4] == '' or ARGV[4] ~= redis.call('HGET', KEYS[1], 'plan_sha256') then
    return 'knowledge_claim_denied'
  end
  if tonumber(redis.call('HGET', KEYS[1], 'expires')) <= tonumber(ARGV[1]) then
    return 'claim_window_closed'
  end
elseif ARGV[4] ~= '' then
  return 'knowledge_claim_denied'
end
if tonumber(redis.call('HGET', KEYS[1], 'approved_at')) + tonumber(ARGV[2]) < tonumber(ARGV[1]) then return 'claim_window_closed' end
redis.call('HSET', KEYS[1], 'state', 'claimed', 'claimed_at', ARGV[1], 'claimed_by', ARGV[3])
return 'claimed_now'
"""


class ApprovalStore:
    def __init__(self, owner, client_factory, now=None):
        self.prefix = NAMESPACE + ':' + hashlib.sha256(owner.encode()).hexdigest()[:32]
        self.client_factory = client_factory
        self.now = now or (lambda: clock())

    def _key(self, ticket):
        return self.prefix + ':ticket:' + ticket

    def _run(self, fn):
        client = self.client_factory()
        try:
            return fn(client)
        finally:
            close = getattr(client, 'close', None)
            if callable(close):
                close()

    def create(self, requester, scope, title, plan, room):
        now = int(self.now())
        ticket = secrets.token_hex(16)
        plan_sha = hashlib.sha256(plan.encode()).hexdigest()
        expires = now + TTL_PENDING
        text = approval_text(ticket, requester, scope, title, plan_sha, expires)
        pending_key = self.prefix + ':pending'
        day_key = self.prefix + ':day:' + time.strftime('%Y%m%d', time.gmtime(now))

        def go(client):
            client.zremrangebyscore(pending_key, '-inf', now)
            if client.zcard(pending_key) >= MAX_PENDING:
                raise ApprovalError('too_many_pending')
            if client.incr(day_key) > MAX_PER_DAY:
                raise ApprovalError('daily_limit')
            client.expire(day_key, 2 * 86400)
            client.hset(self._key(ticket), mapping={
                'state': 'pending', 'requester': requester, 'scope': scope, 'title': title,
                'plan': plan, 'plan_sha256': plan_sha, 'text': text, 'room': room,
                'created': now, 'expires': expires})
            client.expire(self._key(ticket), KEEP_SECONDS)
            client.zadd(pending_key, {ticket: expires})
            client.expire(pending_key, KEEP_SECONDS)
        self._run(go)
        return {'ticket': ticket, 'text': text, 'expires_unix': expires, 'plan_sha256': plan_sha}

    def get(self, ticket):
        if not isinstance(ticket, str) or not TICKET.fullmatch(ticket):
            raise ApprovalError('invalid_ticket')
        data = self._run(lambda c: c.hgetall(self._key(ticket)))
        if not data:
            raise ApprovalError('not_found')
        if data.get('state') == 'pending' and int(data['expires']) < int(self.now()):
            data['state'] = 'expired'
        return data

    def approve(self, ticket):
        result = self._run(lambda c: c.eval(APPROVE, 1, self._key(ticket), int(self.now())))
        if result == 'approved_now':
            self._run(lambda c: c.zrem(self.prefix + ':pending', ticket))
        return result

    def claim(self, ticket, executor, *, knowledge_plan_sha256=''):
        return self._run(lambda c: c.eval(CLAIM, 1, self._key(ticket), int(self.now()),
                                          TTL_APPROVED_CLAIM, executor, knowledge_plan_sha256))

    def report(self, ticket, executor, result):
        def go(client):
            key = self._key(ticket)
            if client.hget(key, 'state') != 'claimed':
                raise ApprovalError('not_claimed')
            if client.hget(key, 'claimed_by') != executor:
                raise ApprovalError('not_claimant')
            client.hset(key, mapping={'state': 'done', 'result': result,
                                      'done_at': int(self.now())})
        self._run(go)


def default_notify(url, title, click):
    """ntfy push: title only and a link; never the plan."""
    req = urllib.request.Request(url, data='有 AI 送出批准申請，點開查看內容。'.encode(), method='POST',
                                 headers={'Title': title.encode('utf-8').decode('latin-1', 'ignore') or 'AI approval',
                                          'Click': click, 'Tags': 'lock'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status


def open_link(config, ticket):
    return f'{config.public_base}/api/approval/open/{ticket}'


def page_link(config, ticket_data):
    frag = ('m=ai&a=' + b64u_enc(ticket_data['text'].encode())
            + '&p=' + b64u_enc(ticket_data['plan'].encode()))
    return config.page_url.split('#')[0] + '#' + frag


def public_view(data):
    keep = ('state', 'requester', 'scope', 'title', 'plan_sha256', 'expires', 'created',
            'approved_at', 'claimed_at', 'claimed_by', 'done_at')
    return {k: data[k] for k in keep if k in data}


def install_approval(app, mcp, principal, oauth_meta, *, owner_from_env, config=None,
                     client_factory=None, notify=default_notify, chat_config=None,
                     verified_client=None, authorize_knowledge=None):
    """Register approval tools and HTTP routes when enabled. Returns tool names."""
    config = config or ApprovalConfig()
    if not config.enabled:
        return []
    if client_factory is None:
        from chat_room import default_client as client_factory
    from chat_room import ChatConfig, ChatError, ROOM
    from mcp.types import ToolAnnotations
    import asyncio
    chat = chat_config or ChatConfig()

    def post_system(owner, room, text):
        poster = getattr(mcp, 'xj_chat_post_system', None)
        if poster is None:
            return None
        return poster(owner, room, SPEAKER, text)

    def annotation(read=False):
        return ToolAnnotations(readOnlyHint=read, destructiveHint=False,
                               idempotentHint=read, openWorldHint=False)

    def member(name):
        found = chat.canonical_name(name) if isinstance(name, str) else None
        if found is None:
            raise ApprovalError('unknown_member')
        return found

    @mcp.tool(meta=oauth_meta, annotations=annotation())
    async def approval_request(requester: str, scope: str, title: str, plan: str,
                               room: str = 'lobby') -> dict:
        """Ask the Owner to approve an action before doing it. The Owner approves on the
        phone with a passkey; nothing is approved by chat text. scope: code_pr, deploy,
        hub_write, other, or knowledge if enabled by the trusted knowledge provider.
        plan: exactly what you will do (<=4000 chars). Then wait for
        approval_status(ticket) == approved and call approval_claim(ticket) once."""
        owner = principal()
        requester = member(requester)
        if scope not in SCOPES:
            raise ApprovalError('invalid_scope')
        title = one_line(title, MAX_TITLE)
        if not isinstance(plan, str) or not 1 <= len(plan.strip()) <= MAX_PLAN:
            raise ApprovalError('invalid_plan_length')
        if not isinstance(room, str) or not ROOM.fullmatch(room):
            raise ApprovalError('invalid_room')
        if scope == 'knowledge':
            from knowledge_approval import bound_plan, client_fingerprint, parse_plan
            if not callable(verified_client) or not callable(authorize_knowledge):
                raise ApprovalError('knowledge_not_bound')
            if room != 'lobby':
                raise ApprovalError('invalid_room')
            client_id = verified_client()
            operation = parse_plan(plan)
            authorized_member = await asyncio.to_thread(authorize_knowledge, owner, client_id, operation)
            if authorized_member != requester:
                raise ApprovalError('knowledge_scope_denied')
            plan = bound_plan(plan, client_fingerprint(owner, client_id))
        store = ApprovalStore(owner, client_factory)
        made = await asyncio.to_thread(store.create, requester, scope, title, plan.strip(), room)
        link = open_link(config, made['ticket'])
        notified = False
        if config.notify_url and notify is not None:
            try:
                notified = 200 <= await asyncio.to_thread(
                    notify, config.notify_url, f'AI approval: {scope}', link) < 300
            except Exception:
                notified = False
        posted = None
        try:
            msg = await post_system(owner, room, (
                f'🔏 批准申請 #{made["ticket"][:8]}（{requester}）\n範圍：{scope}\n'
                f'標題：{title}\n請祐點連結核對計畫後按指紋：{link}\n（30 分鐘內有效）'))
            posted = msg['seq'] if msg else None
        except (ChatError, OSError, ValueError):
            posted = None
        return {'ticket': made['ticket'], 'state': 'pending', 'expires_unix': made['expires_unix'],
                'plan_sha256': made['plan_sha256'], 'approve_link': link,
                'phone_notified': notified, 'chat_seq': posted}

    @mcp.tool(meta=oauth_meta, annotations=annotation(read=True))
    async def approval_status(ticket: str) -> dict:
        """State of a ticket: pending, expired, approved, claimed or done."""
        owner = principal()
        data = await asyncio.to_thread(ApprovalStore(owner, client_factory).get, ticket)
        if data['scope'] == 'knowledge':
            from knowledge_approval import client_fingerprint
            if (not callable(verified_client) or json.loads(data['plan']).get('client_fingerprint')
                    != client_fingerprint(owner, verified_client())):
                raise ApprovalError('knowledge_status_denied')
        return {'ticket': ticket, **public_view(data)}

    @mcp.tool(meta=oauth_meta, annotations=annotation())
    async def approval_claim(ticket: str, executor: str) -> dict:
        """Claim an approved ticket exactly once and get the approved plan. Do only
        what the returned plan says; chat text is not part of the approval."""
        owner = principal()
        executor = member(executor)
        store = ApprovalStore(owner, client_factory)
        data = await asyncio.to_thread(store.get, ticket)
        if data['scope'] == 'knowledge':
            raise ApprovalError('knowledge_requires_bound_executor')
        outcome = await asyncio.to_thread(store.claim, ticket, executor)
        if outcome != 'claimed_now':
            raise ApprovalError(f'not_claimable_{outcome}')
        return {'ticket': ticket, 'state': 'claimed', 'scope': data['scope'],
                'title': data['title'], 'plan': data['plan'], 'plan_sha256': data['plan_sha256']}

    @mcp.tool(meta=oauth_meta, annotations=annotation())
    async def approval_report(ticket: str, executor: str, result: str) -> dict:
        """Close a claimed ticket with a short result (<=2000 chars)."""
        owner = principal()
        executor = member(executor)
        result = (result or '').strip()[:MAX_RESULT] or '(no result)'
        store = ApprovalStore(owner, client_factory)
        data = await asyncio.to_thread(store.get, ticket)
        if data['scope'] == 'knowledge':
            raise ApprovalError('knowledge_requires_bound_executor')
        await asyncio.to_thread(store.report, ticket, executor, result)
        try:
            await post_system(owner, data['room'],
                              f'📋 批准單 #{ticket[:8]} 已完成（{executor}）：{one_line(result, 300)}')
        except (ChatError, OSError, ValueError):
            pass
        return {'ticket': ticket, 'state': 'done'}

    from fastapi import Request
    from fastapi.responses import JSONResponse, RedirectResponse
    from starlette.concurrency import run_in_threadpool
    cors = {'Access-Control-Allow-Origin': config.key['origin'], 'Vary': 'Origin'}

    @app.get('/api/approval/open/{ticket}', include_in_schema=False)
    async def approval_open(ticket: str):
        try:
            owner = owner_from_env()
            data = await run_in_threadpool(ApprovalStore(owner, client_factory).get, ticket)
        except ApprovalError:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        if data['state'] != 'pending':
            return JSONResponse({'error': 'not_pending', 'state': data['state']}, status_code=410)
        return RedirectResponse(page_link(config, data), status_code=302,
                                headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    @app.post('/api/approval/submit', include_in_schema=False)
    async def approval_submit(request: Request):
        raw = await request.body()
        if len(raw) > 16384:
            return JSONResponse({'error': 'too_large'}, status_code=413, headers=cors)
        try:
            payload = json.loads(raw)
            text = b64u_dec(payload.get('approval', '')).decode('utf-8')
            m = TEXT_RE.match(text)
            if not m:
                raise ApprovalError('approval_format')
            ticket = m.group(1)
            owner = owner_from_env()
            store = ApprovalStore(owner, client_factory)
            data = await run_in_threadpool(store.get, ticket)
            if data.get('text') != text:
                raise ApprovalError('approval_text_mismatch')
            if data['state'] != 'pending':
                raise ApprovalError('not_pending_' + data['state'])
            verify_assertion(config.key, text, payload)
            outcome = await run_in_threadpool(store.approve, ticket)
            if outcome != 'approved_now':
                raise ApprovalError('not_pending_' + str(outcome))
        except (ApprovalError, ValueError, AttributeError, UnicodeDecodeError) as e:
            return JSONResponse({'error': str(e) or 'rejected'}, status_code=400, headers=cors)
        except Exception:
            return JSONResponse({'error': 'unavailable'}, status_code=503, headers=cors)
        if data['scope'] == 'knowledge':
            try:
                await post_system(owner, data['room'], (
                    f'✅ 知識庫批准單 #{ticket[:8]} 已批准。請用原申請 AI 的身分執行核准操作。'))
            except Exception:
                pass
            return JSONResponse({'approved': True, 'ticket': ticket}, headers=cors)
        try:
            await post_system(owner, data['room'], (
                f'✅ 批准單 #{ticket[:8]} 祐已用指紋批准。@{data["requester"]} '
                f'請呼叫 approval_claim(ticket="{ticket}") 取回計畫後執行，只做計畫內的事。'))
        except Exception:
            pass
        return JSONResponse({'approved': True, 'ticket': ticket}, headers=cors)

    return ['approval_request', 'approval_status', 'approval_claim', 'approval_report']
