"""Same Redis injected by original host. No URL/secret acquisition or new Redis."""
import hashlib
class RedisAttempts:
 def __init__(self,client):self.client=client
 def key(self,scope,kind,identifier):
  return 'xj:s2s-receiver:v1:{'+scope+'}:'+kind+':'+hashlib.sha256(identifier.encode()).hexdigest()
 def nonce(self,scope,nonce,ttl):return bool(self.client.set(self.key(scope,'nonce',nonce),'1',nx=True,ex=ttl))
 def begin(self,scope,op,digest,ttl):return bool(self.client.set(self.key(scope,'op',op),'PENDING:'+digest,nx=True,ex=ttl))
 def finish(self,scope,op,digest):
  value=self.client.eval("if redis.call('GET',KEYS[1]) == ARGV[1] then redis.call('SET',KEYS[1],ARGV[2],'KEEPTTL'); return 1 else return 0 end",1,self.key(scope,'op',op),'PENDING:'+digest,'COMPLETED:'+digest)
  if value!=1:raise RuntimeError('RECEIPT_UNCONFIRMED')
 def status(self,scope,op):
  value=self.client.get(self.key(scope,'op',op))
  if isinstance(value,bytes):value=value.decode('ascii')
  return {'state':'NOT_FOUND_UNKNOWN' if value is None else ('COMPLETED' if value.startswith('COMPLETED:') else 'PENDING_UNKNOWN')}
