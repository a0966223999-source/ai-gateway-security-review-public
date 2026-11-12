"""Original preview host composition. No network at import; no new database.
Requires explicit public grant configuration after approval. Never log env.
"""
import base64
import hashlib
import json
import os
from urllib.parse import urlsplit
from s2s import Grant, Service, ORIGIN, PATH
from store import RedisAttempts
from grant_registry import scope_digest,registry_key,enroll_once


def build_original_service(*, env=os.environ, redis_factory=None, inbox_class=None, approved_bootstrap=False):
    if env.get('VERCEL_ENV') != 'preview':
        raise RuntimeError('ORIGINAL_PREVIEW_REQUIRED')
    if redis_factory is None:
        from redis import Redis
        redis_factory = Redis.from_url
    if inbox_class is None:
        from inbox import Inbox
        inbox_class = Inbox
    url = env.get('XJ_PREVIEW_REDIS_URL') or env.get('REDIS_URL','')
    endpoint=urlsplit(url)
    if endpoint.scheme!='rediss' or not endpoint.hostname or endpoint.query or endpoint.fragment:
        raise RuntimeError('ORIGINAL_REDIS_CONFIGURATION_REQUIRED')
    # Same existing credential source as original Inbox, never caller supplied.
    redis=redis_factory(url,decode_responses=True,ssl_cert_reqs='required',
                       ssl_check_hostname=True,socket_connect_timeout=3,socket_timeout=3)
    def configured_grant(kid):
        try:
            value=json.loads(env['XJ_INTERNAL_INBOX_PUBLIC_GRANT'])
            if set(value)!={'kid','publicKey','mailbox','expires','enabled'}:return None
            if value['kid']!=kid or type(value['expires']) is not int or value['enabled'] is not True:return None
            domain=env['AUTH0_DOMAIN'].rstrip('/')
            if '://' in domain or '/' in domain or not domain:return None
            issuer='https://'+domain+'/'
            subject=env['XJ_AUTH0_ALLOWED_SUBJECT']
            if not isinstance(subject,str) or not subject.strip():return None
            owner='oauth:'+hashlib.sha256((issuer+'|'+subject).encode()).hexdigest()
            public=base64.b64decode(value['publicKey'],validate=True)
            if len(public)!=32:return None
            return Grant(kid,public,owner,value['mailbox'],value['expires'],True)
        except (KeyError,ValueError,TypeError):return None
    def grant(kid):
        candidate=configured_grant(kid)
        if candidate is None:return None
        value=json.loads(env['XJ_INTERNAL_INBOX_PUBLIC_GRANT'])
        marker=redis.hget(registry_key(kid),'active')
        if isinstance(marker,bytes):marker=marker.decode('ascii')
        return candidate if marker==scope_digest(value,candidate.owner) else None

    def activate(candidate):
        # Called only after signature, expiry, action and argument verification.
        # Recheck configuration so a concurrent revoke/change cannot grant access.
        if configured_grant(candidate.kid)!=candidate:return None
        if approved_bootstrap is True:
            value=json.loads(env['XJ_INTERNAL_INBOX_PUBLIC_GRANT'])
            try:enroll_once(redis,value,candidate.owner,approved=True)
            except ValueError:return None
        return grant(candidate.kid)

    return Service(current_grant=grant,initial_grant=configured_grant,
                   activate_grant=activate,
                   inbox_factory=lambda owner,box:inbox_class(owner,box,client=redis),
                   attempts=RedisAttempts(redis))
