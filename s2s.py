"""Offline restricted S2S service core; no credential generation or auto-install.
Original host injects Inbox and same-Redis durable attempt store. No send scope.
"""
import base64,hashlib,json,time,re
from dataclasses import dataclass
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PATH='/api/internal/inbox'
ORIGIN='https://ai-gateway-security-review-public-git-test-ver-e7ace3-yoyo-5560.vercel.app'
ACTIONS={'list','claim','ack','status'}

def canonical(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
class Refused(RuntimeError):pass
@dataclass(frozen=True)
class Grant:
 kid:str
 public_key:bytes
 owner:str
 mailbox:str
 expires:int
 enabled:bool


def signing_bytes(request):return b'POST\n'+ORIGIN.encode()+b'\n'+PATH.encode()+b'\n'+canonical(request)

class Service:
 def __init__(self,*,current_grant,inbox_factory,attempts,clock=time.time,initial_grant=None,activate_grant=None):
  self.grant=current_grant;self.inbox=inbox_factory;self.attempts=attempts;self.clock=clock
  self.initial_grant=initial_grant or current_grant;self.activate_grant=activate_grant
 def handle(self,request,signature):
  if not isinstance(request,dict) or set(request)!={'kid','mailbox_id','action','args','timestamp','nonce','operation_id'}:raise Refused('INVALID_REQUEST')
  try:encoded=canonical(request)
  except (ValueError,TypeError):raise Refused('INVALID_REQUEST') from None
  if len(encoded)>4096:raise Refused('REQUEST_TOO_LARGE')
  for n in ('kid','nonce','operation_id'):
   v=request[n]
   if not isinstance(v,str) or not 16<=len(v)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in v):raise Refused('INVALID_IDENTIFIER')
  if type(request['timestamp']) is not int or abs(self.clock()-request['timestamp'])>30:raise Refused('REQUEST_EXPIRED')
  g=self.initial_grant(request['kid'])
  if not isinstance(g,Grant) or g.enabled is not True or g.expires<=self.clock() or g.kid!=request['kid'] or request['mailbox_id']!=g.mailbox:raise Refused('GRANT_DENIED')
  if not isinstance(signature,str) or len(signature)>256:raise Refused('SIGNATURE_DENIED')
  try:Ed25519PublicKey.from_public_bytes(g.public_key).verify(base64.b64decode(signature,validate=True),signing_bytes(request))
  except Exception:raise Refused('SIGNATURE_DENIED') from None
  action=request['action'];args=request['args']
  if not isinstance(action,str) or action not in ACTIONS or not isinstance(args,dict):raise Refused('ACTION_DENIED')
  expected={'list':{'cursor','limit'},'claim':{'event_id'},'ack':{'event_id','claim_token','version'},'status':{'target_operation_id'}}[action]
  if set(args)!=expected:raise Refused('ARGUMENTS_DENIED')
  if action=='list' and (type(args['limit']) is not int or not 1<=args['limit']<=100 or not isinstance(args['cursor'],str) or len(args['cursor'])>64 or (args['cursor'] and re.fullmatch(r'([0-9a-f]{32}):([0-9]{1,15})',args['cursor']) is None)):raise Refused('ARGUMENTS_DENIED')
  if action in ('claim','ack') and (not isinstance(args['event_id'],str) or len(args['event_id'])!=64 or any(c not in '0123456789abcdef' for c in args['event_id'])):raise Refused('ARGUMENTS_DENIED')
  if action=='ack' and (type(args['version']) is not int or args['version']<1 or not isinstance(args['claim_token'],str) or not 20<=len(args['claim_token'])<=100):raise Refused('ARGUMENTS_DENIED')
  if action=='status' and (not isinstance(args['target_operation_id'],str) or not 16<=len(args['target_operation_id'])<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args['target_operation_id'])):raise Refused('ARGUMENTS_DENIED')
  if g.expires<=self.clock():raise Refused('GRANT_DENIED')
  if self.activate_grant is not None:
   if self.activate_grant(g)!=g:raise Refused('GRANT_ACTIVATION_DENIED')
  if self.grant(g.kid)!=g or g.expires<=self.clock():raise Refused('GRANT_CHANGED')
  scope=hashlib.sha256(canonical([g.kid,g.owner,g.mailbox])).hexdigest()
  # Store only a digest of arguments, never claim token or result plaintext.
  digest=hashlib.sha256(canonical([action,args])).hexdigest()
  if not self.attempts.nonce(scope,request['nonce'],ttl=90):raise Refused('REPLAY_DENIED')
  if self.grant(g.kid)!=g:raise Refused('GRANT_CHANGED')
  if action=='status':
   result=self.attempts.status(scope,args['target_operation_id'])
   if self.grant(g.kid)!=g or g.expires<=self.clock():raise Refused('RESULT_WITHHELD_GRANT_CHANGED')
   return result
  if not self.attempts.begin(scope,request['operation_id'],digest,ttl=86400):raise Refused('ATTEMPT_EXISTS_QUERY_ONLY')
  try:
   if self.grant(g.kid)!=g or g.expires<=self.clock():raise Refused('GRANT_CHANGED')
   box=self.inbox(g.owner,g.mailbox)
   if box.owner!=g.owner or box.mailbox_id!=g.mailbox:raise Refused('INBOX_BINDING_CHANGED')
   if action=='list':result=box.list(**args)
   elif action=='claim':result=box.claim(args['event_id'])
   else:result=box.settle('ack',args['event_id'],args['claim_token'],args['version'])
   self.attempts.finish(scope,request['operation_id'],digest)
  except Exception:raise Refused('OUTCOME_UNKNOWN_QUERY_ONLY') from None
  if self.grant(g.kid)!=g or g.expires<=self.clock():raise Refused('RESULT_WITHHELD_GRANT_CHANGED')
  return result
