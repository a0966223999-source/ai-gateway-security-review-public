"""Explicit once-only public grant enrollment in original Redis.
Retain enrolled tombstone forever for this kid. Revoke by HDEL active, never DEL
registry. First deployment opt-in is mandatory; no new Redis or credentials.
"""
import hashlib,json
from s2s import ORIGIN,PATH

def scope_digest(value,owner):
 return hashlib.sha256(json.dumps({'grant':value,'owner':owner,'origin':ORIGIN,'path':PATH},sort_keys=True,separators=(',',':')).encode()).hexdigest()
def registry_key(kid):
 if not isinstance(kid,str) or not 16<=len(kid)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in kid):raise ValueError('INVALID_KID')
 return 'xj:s2s-grant:v1:{'+kid+'}'
SCRIPT="""if redis.call('HEXISTS',KEYS[1],'enrolled') == 1 then return 0 end
redis.call('HSET',KEYS[1],'enrolled',ARGV[1],'active',ARGV[1]); return 1"""
def enroll_once(redis,value,owner,*,approved=False):
 if approved is not True:raise ValueError('EXPLICIT_APPROVAL_REQUIRED')
 digest=scope_digest(value,owner);key=registry_key(value['kid'])
 result=redis.eval(SCRIPT,1,key,digest)
 if result not in (0,1):raise ValueError('GRANT_ENROLLMENT_UNKNOWN')
 marker=redis.hget(key,'active')
 if isinstance(marker,bytes):marker=marker.decode('ascii')
 if marker!=digest:raise ValueError('GRANT_REVOKED_OR_CONFLICT_QUERY_ONLY')
 return result==1
