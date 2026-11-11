-- Bounded synthetic mailbox; one cluster-safe key, one atomic transition.
local action = ARGV[1]
local req = cjson.decode(ARGV[2])
local now = tonumber(redis.call('TIME')[1])
local raw = redis.call('GET', KEYS[1])
local doc = raw and cjson.decode(raw) or {epoch=req.epoch,seq=0,messages={},subscriptions={},deliveries={}}
local function result(value) return cjson.encode(value) end
local function error(code) return result({error=code}) end
local function save(value)
 redis.call('SET',KEYS[1],cjson.encode(doc),'EX',86400)
 return result(value)
end
local function event_name(v) return v.event_name or 'inbox.message.created' end
local function active(s) return s and s.expires_at>now and s.access_until>now end
local function count(t) local n=0 for _ in pairs(t) do n=n+1 end return n end
-- Record deadlines are independent of the outer document's idle expiry.
if action=='send' or action=='rpc_submit' or action=='subscribe' then
 for id,m in pairs(doc.messages) do if m.expires_at<=now then doc.messages[id]=nil end end
 for id,d in pairs(doc.deliveries) do if d.expires_at<=now then doc.deliveries[id]=nil end end
 for id,s in pairs(doc.subscriptions) do if not active(s) then doc.subscriptions[id]=nil end end
end
if action=='subscription_status' then
 if req.event_name~='gateway.continuation.ready' then return error('formal_event_required') end
 local items={}
 for sid,s in pairs(doc.subscriptions) do
  if event_name(s)=='gateway.continuation.ready' then
   if type(sid)~='string' or #sid~=68 or not string.match(sid,'^sub_[0-9a-f]+$')
      or type(s.expires_at)~='number' or type(s.access_until)~='number' then
    return error('subscription_metadata_invalid')
   end
   table.insert(items,{id=sid,event_name='gateway.continuation.ready',
    expires_at=s.expires_at,access_until=s.access_until,active=active(s) and true or false})
  end
 end
 table.sort(items,function(a,b) return a.id<b.id end)
 return result({items=items,count=#items,record_present=raw~=false and raw~=nil,
  observed_at=now,record_ttl_seconds=redis.call('TTL',KEYS[1]),
  coverage='retained_records_only',historical_completeness=false,
  original_create_outcome='UNKNOWN'})
elseif action=='send' or action=='rpc_submit' then
 local rpc=action=='rpc_submit'
 if rpc then
  if req.event_name~='gateway.continuation.ready' or type(req.rpc_request)~='string'
   or #req.rpc_request>65536 or #req.rpc_request==0 or type(req.rpc_sha256)~='string'
   or #req.rpc_sha256~=64 or type(req.rpc_deadline)~='number' or req.rpc_deadline<=now
   or req.rpc_deadline>now+600 or type(req.rpc_deadline_string)~='string'
   or tonumber(req.rpc_deadline_string)~=req.rpc_deadline then return error('rpc_scope') end
  if req.rpc_target_locator~=nil then
   if type(req.rpc_target_locator)~='string' or #req.rpc_target_locator==0
    or #req.rpc_target_locator>2048 then return error('rpc_target_locator') end
   local ok,t=pcall(cjson.decode,req.rpc_target_locator)
   local request_ok,body=pcall(cjson.decode,req.rpc_request)
   if not ok or type(t)~='table' or t.kind~='ordinary-work'
    or type(t.threadId)~='string' or #t.threadId==0 or #t.threadId>256
    or not request_ok or type(body)~='table' or body.method~='register' then return error('rpc_target_locator') end
   local native=(t.v==2 and count(t)==4 and t.route==nil) or (t.v==3 and t.route=='native-chat' and count(t)==5)
   local cloud=t.v==3 and t.route=='cloud-task' and count(t)==4 and t.environmentId==nil
   if not native and not cloud then return error('rpc_target_locator') end
   if native and (type(t.environmentId)~='string' or #t.environmentId==0 or #t.environmentId>256) then return error('rpc_target_locator') end

  end
  local q=cjson.decode(req.message)
  if q.kind~='source-finalize-rpc-v1' or q.requestSHA256~=req.rpc_sha256
   or q.deadline~=req.rpc_deadline or q.nonce~=req.rpc_nonce then return error('rpc_locator') end
 end
 local name=req.event_name or 'inbox.message.created'
 if name~='inbox.message.created' and name~='gateway.continuation.ready' then return error('unknown_event') end
 local m=nil
 for _,existing in pairs(doc.messages) do
  if existing.operation_key==req.operation_key and event_name(existing)==name then m=existing; break end
 end
 if m then
  if rpc and (m.rpc_request~=req.rpc_request or m.rpc_request_sha256~=req.rpc_sha256 or m.rpc_target_locator~=req.rpc_target_locator) then return error('rpc_operation_conflict') end
  if not rpc and m.rpc_request then return error('rpc_operation_reserved') end
  if rpc then return result({event_id=m.event_id,duplicate=true,expires_at=m.expires_at}) end
  if m.digest~=req.digest then return error('operation_conflict') end
  return result({event_id=m.event_id,duplicate=true,expires_at=m.expires_at})
 end
 if count(doc.messages)>=100 or count(doc.deliveries)+count(doc.subscriptions)>1000 then return error('mailbox_full') end
 doc.seq=doc.seq+1
 m={event_name=name,event_id=req.event_id,digest=req.digest,operation_key=req.operation_key,message=req.message,
    sequence=doc.seq,created_at=now,expires_at=now+86400,state='available',version=0}
 if rpc then
  if req.rpc_target_locator~=nil then m.rpc_target_locator=req.rpc_target_locator end
  m.rpc_request=req.rpc_request; m.rpc_request_sha256=req.rpc_sha256
  m.rpc_state='reserved_query_only'; m.rpc_nonce=req.rpc_nonce; m.rpc_deadline=req.rpc_deadline_string
 end
 doc.messages[m.event_id]=m
 for sid,s in pairs(doc.subscriptions) do
  if active(s) and event_name(s)==name then
   local did=m.event_id..':'..sid
   doc.deliveries[did]={delivery_id=did,event_id=m.event_id,subscription_id=sid,
    event_name=name,created_at=now,expires_at=m.expires_at,state='pending',attempts=0}
  end
 end
 return save({event_id=m.event_id,duplicate=false,expires_at=m.expires_at})
elseif action=='list' then
 if not raw and req.cursor_epoch=='' then return result({items={},cursor='',has_more=false}) end
 if req.cursor_epoch~='' and req.cursor_epoch~=doc.epoch then return error('cursor_expired') end
 local items={}
 for _,m in pairs(doc.messages) do
  if m.expires_at>now and m.sequence>req.after and event_name(m)==(req.event_name or 'inbox.message.created') then
   local state=m.state
   if state=='claimed' and m.lease_until<=now then state='available' end
   table.insert(items,{event_id=m.event_id,operation_key=m.operation_key,message=m.message,
    sequence=m.sequence,state=state,created_at=m.created_at,expires_at=m.expires_at})
  end
 end
 table.sort(items,function(a,b) return a.sequence<b.sequence end)
 local more=#items>req.limit
 while #items>req.limit do table.remove(items) end
 local last=#items>0 and items[#items].sequence or req.after
 return result({items=items,cursor=doc.epoch..':'..last,has_more=more})
elseif action=='claim' or action=='ack' or action=='release' then
 local m=doc.messages[req.event_id]
 if not m or m.expires_at<=now or event_name(m)~=(req.event_name or 'inbox.message.created') then return error('message_missing') end
 if action=='claim' then
  if m.state=='completed' then return error('already_completed') end
  if m.state=='claimed' and m.lease_until>now then return error('lease_busy') end
  m.version=m.version+1; m.state='claimed';m.lease_digest=req.lease_digest
  m.lease_until=math.min(now+120,m.expires_at)
  return save({event_id=m.event_id,state=m.state,version=m.version,lease_until=m.lease_until})
 end
 if m.lease_digest~=req.lease_digest or m.version~=req.version then return error('lease_rejected') end
 if action=='ack' and m.state=='completed' then return result({event_id=m.event_id,state=m.state}) end
 if action=='release' and m.state=='available' then return result({event_id=m.event_id,state=m.state}) end
 if m.state~='claimed' or m.lease_until<=now then return error('lease_rejected') end
 m.state=action=='ack' and 'completed' or 'available'
 return save({event_id=m.event_id,state=m.state})
elseif action=='subscription_get' then
 local s=doc.subscriptions[req.id]
 if active(s) then return result({found=true,subscription=s}) end
 return result({found=false})
elseif action=='subscribe' then
 local old=doc.subscriptions[req.subscription.id]
 if not old and count(doc.subscriptions)>=10 then return error('subscription_limit') end
 if req.expected_revision~=(old and old.revision or '') then return error('subscription_changed') end
 doc.subscriptions[req.subscription.id]=req.subscription
 return save({id=req.subscription.id})
elseif action=='unsubscribe' then
 doc.subscriptions[req.id]=nil
 return save({})
elseif action=='delivery_status' then
 local items={}
 for _,d in pairs(doc.deliveries) do
  if d.expires_at>now and event_name(d)==(req.event_name or 'inbox.message.created') and (req.delivery_id=='' or req.delivery_id==d.delivery_id) then
   table.insert(items,{event_name=event_name(d),delivery_id=d.delivery_id,event_id=d.event_id,state=d.state,
    attempts=d.attempts,last_status=d.last_status,expires_at=d.expires_at,
    retry_after=d.state=='sending' and d.lock_until or nil})
  end
 end
 table.sort(items,function(a,b) return a.delivery_id<b.delivery_id end)
 return result({items=items})
elseif action=='delivery_acquire' then
 local d=doc.deliveries[req.delivery_id]
 if not d or d.expires_at<=now then return error('delivery_missing') end
 local s=doc.subscriptions[d.subscription_id]
 if not active(s) then return error('subscription_inactive') end
 if event_name(d)~=event_name(s) then return error('event_type_mismatch') end
 if d.state=='delivered' or d.state=='terminal' then return error('delivery_final') end
 if d.state=='sending' and d.lock_until>now then return error('delivery_busy') end
 d.state='sending';d.lock_until=now+20;d.lock=req.lock
 return save({delivery=d,subscription=s})
elseif action=='delivery_finish' then
 local d=doc.deliveries[req.delivery_id]
 if not d or d.lock~=req.lock or d.lock_until<=now then return error('delivery_lock_lost') end
 d.state=req.state;d.attempts=d.attempts+req.attempts;d.last_status=req.status
 d.lock=nil;d.lock_until=nil
 return save({delivery_id=d.delivery_id,state=d.state,attempts=d.attempts})
end
return error('invalid_action')
