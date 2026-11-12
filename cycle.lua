-- Same original mailbox JSON key. Caller validates new fixed-host signatures.
-- Uses Redis TIME, never caller-selected time. Does not call native Work/Source.
local req=cjson.decode(ARGV[1])
local raw=redis.call('GET',KEYS[1])
local function out(v) return cjson.encode(v) end
local function err(code) return out({error=code}) end
if not raw then return err('MAILBOX_MISSING') end
local doc=cjson.decode(raw)
local now=tonumber(redis.call('TIME')[1])
local m=doc.messages[req.event_id]
if not m or m.event_name~='gateway.continuation.ready' then return err('ORIGINAL_EVENT_MISSING') end
local function save(v)
 redis.call('SET',KEYS[1],cjson.encode(doc),'KEEPTTL')
 return out(v)
end
local function count(t) local n=0;for _ in pairs(t) do n=n+1 end;return n end
doc.continuation_cycles=doc.continuation_cycles or {}
local c=doc.continuation_cycles[req.event_id]
local function summary()
 if not c then return {status='CYCLE_MISSING',task_completed=false} end
 local successor=nil
 if c.successor then
  successor={}
  for k,v in pairs(c.successor) do if k~='lock' then successor[k]=v end end
 end
 return {status=c.state,generation=c.generation,receiver_run_id=c.receiver_run_id,
  started_at=c.started_at,round_deadline=c.round_deadline,scope_expires_at=c.scope_expires_at,
  task_completed=c.agent_reported_complete==true,agent_reported_complete=c.agent_reported_complete==true,
  businessAcceptanceVerified=false,parentCloseAllowed=false,decision=c.decision,reason=c.reason,
  successor=successor,completed_round=c.completed_round,policy=c.policy,
  binding=c.binding,subscription_id=c.subscription_id,watch=c.watch,phase=c.phase}
end
if req.action=='query' then return out(summary()) end
if now>=m.expires_at then return err('ORIGINAL_EVENT_EXPIRED') end
if req.action=='begin' and not c then
 if req.generation~=0 or req.binding.logical_event_id~=req.event_id or req.binding.mailbox_operation_key~=m.operation_key or req.binding.mailbox_digest~=m.digest then return err('INITIAL_BINDING') end
 -- Match original mailbox expiry cleanup, but never erase a still-valid or
 -- uncertain event's dedupe record merely because this control scope stopped.
 for eid,old in pairs(doc.continuation_cycles) do
  local original=doc.messages[eid]
  if type(old.original_event_expires_at)=='number' and old.original_event_expires_at<=now and
     (not original or (type(original.expires_at)=='number' and original.expires_at<=now)) then
   doc.continuation_cycles[eid]=nil
  end
 end
 if count(doc.continuation_cycles)>=100 then return err('CYCLE_LIMIT') end
 local s=doc.subscriptions[req.subscription_id]
 local d=doc.deliveries[req.event_id..':'..req.subscription_id]
 if not s or s.event_name~='gateway.continuation.ready' or s.expires_at<=now or s.access_until<=now or not d or not (d.state=='delivered' or (d.state=='sending' and d.lock and d.lock_until>now)) then return err('ORIGINAL_SUBSCRIPTION_DELIVERY_REQUIRED') end
 local expiry=math.min(req.scope_expires_at,m.expires_at,s.expires_at,s.access_until)
 if expiry<now+85 then return err('INSUFFICIENT_ROUND_BUDGET') end
 c={state='active',generation=0,receiver_run_id=req.receiver_run_id,started_at=now,
  round_deadline=math.min(expiry,now+req.policy.round_budget_seconds),scope_expires_at=expiry,
  original_event_expires_at=m.expires_at,
  binding=req.binding,binding_sha256=req.binding_sha256,subscription_id=req.subscription_id,
  policy=req.policy,history={},initial_delivery_observation={state=d.state,received_at=now,receiver_run_id=req.receiver_run_id},
  agent_reported_complete=false,watch=req.watch,watch_sha256=req.watch_sha256,
  phase=req.watch.mode=='resumed' and 'post_admission_read_only' or 'producer_wait'}
 doc.continuation_cycles[req.event_id]=c
 return save(summary())
end
if not c then return err('CYCLE_MISSING') end
local function halt(reason)
 c.state='safety_stopped';c.reason=reason;c.agent_reported_complete=false
 local value=summary();value.reason=reason
 return save(value)
end
-- Callback completion may arrive after the next receiver has been admitted.
-- Update only the identified prior delivery, never the current round/watch.
if req.action=='publish_finish' then
 if req.binding_sha256~=c.binding_sha256 then return err('DELIVERY_BINDING_CHANGED') end
 local next=c.successor
 if not next or next.notification_id~=req.notification_id then
  next=nil
  for _,h in pairs(c.history) do
   if h.successor and h.successor.notification_id==req.notification_id then next=h.successor;break end
  end
 end
 if not next or next.lock~=req.lock or (next.state~='sending' and next.state~='admitted') then return err('DELIVERY_LOCK_LOST_QUERY_ONLY') end
 next.delivery_outcome=req.delivered==true and 'delivered' or 'unknown';next.http_status=req.http_status;next.lock=nil
 if next.state~='admitted' then next.state=next.delivery_outcome end
 return save({status=next.state,delivery_outcome=next.delivery_outcome,
  notification_id=next.notification_id,generation=next.generation})
end
-- A verified original admission may start one read-only result phase.
-- This does not reopen a terminal/unknown outcome or renew any original expiry.
if req.action=='begin' and c.state=='original_continuation_required' and c.watch.mode=='producer' and req.watch.mode=='resumed' then
 if req.binding_sha256~=c.binding_sha256 or req.generation~=c.generation then return halt('BINDING_CHANGED') end
 local expiry=math.min(c.scope_expires_at,req.scope_expires_at,m.expires_at)
 if expiry<now+85 then return halt('INSUFFICIENT_ROUND_BUDGET') end
 c.producer_phase={receiver_run_id=c.receiver_run_id,started_at=c.started_at,decision=c.decision}
 c.watch=req.watch;c.watch_sha256=req.watch_sha256;c.phase='post_admission_read_only'
 c.state='active';c.decision=nil;c.receiver_run_id=req.receiver_run_id;c.started_at=now
 c.round_deadline=math.min(expiry,now+c.policy.round_budget_seconds);c.scope_expires_at=expiry
 return save(summary())
end
if c.state=='safety_stopped' or c.state=='agent_reported_complete' or c.state=='failed_requires_review' or c.state=='original_continuation_required' then return out(summary()) end
if now>=c.scope_expires_at then return halt('SCOPE_EXPIRED') end
if req.binding_sha256~=c.binding_sha256 then return halt('BINDING_CHANGED') end
if req.action=='safety_stop' then return halt(req.reason) end
if req.action=='begin' then
 if req.watch_sha256~=c.watch_sha256 then return halt('WATCH_CHANGED') end
 if req.generation==c.generation and req.receiver_run_id==c.receiver_run_id and c.state=='active' then return out(summary()) end
 local next=c.successor
 if not next or req.generation~=next.generation or req.notification_id~=next.notification_id or not (next.state=='delivered' or (next.state=='sending' and next.attempts==1 and next.lock)) or c.state~='between_rounds' then return err('NEXT_ROUND_NOT_DELIVERED') end
 if req.receiver_run_id==c.receiver_run_id then return err('NEW_RECEIVER_RUN_REQUIRED') end
 if now<c.started_at+c.policy.min_interval_seconds then return err('RATE_LIMIT_NO_AUTOMATIC_WAKE') end
 local expiry=math.min(c.scope_expires_at,req.scope_expires_at,m.expires_at)
 if expiry<now+85 then return halt('INSUFFICIENT_ROUND_BUDGET') end
 next.state='admitted';next.received_at=now;next.received_by=req.receiver_run_id
 c.history[tostring(c.generation)]={receiver_run_id=c.receiver_run_id,started_at=c.started_at,successor=c.successor}
 c.generation=req.generation;c.receiver_run_id=req.receiver_run_id;c.started_at=now
 c.scope_expires_at=expiry;c.round_deadline=math.min(expiry,now+c.policy.round_budget_seconds)
 c.state='active';c.successor=nil;c.completed_round=nil
 return save(summary())
end
if req.action=='observe' then
 if req.watch_sha256~=c.watch_sha256 then return halt('WATCH_CHANGED') end
 if req.generation~=c.generation or req.receiver_run_id~=c.receiver_run_id or c.state~='active' then return err('ROUND_BINDING') end
 if now<c.started_at+80 or req.observed_at<c.started_at+80 then return err('EIGHTY_SECONDS_NOT_ELAPSED') end
 if now>=c.round_deadline then return halt('ROUND_DEADLINE') end
 c.scope_expires_at=math.min(c.scope_expires_at,req.scope_expires_at)
 if now>=c.scope_expires_at then return halt('SCOPE_EXPIRED') end
 if req.native_state=='unknown' then return halt('NATIVE_STATE_UNKNOWN') end
 if req.native_state=='ended' then
  c.decision=req.task_decision
  if req.task_decision=='verified_agent_reported_complete' then c.state='agent_reported_complete';c.agent_reported_complete=true
  elseif req.task_decision=='verified_continue' then c.state='original_continuation_required'
  elseif req.task_decision=='failed_requires_review' then c.state='failed_requires_review'
  else return halt('TASK_MESSAGE_UNKNOWN') end
  return save(summary())
 end
 if req.native_state~='active' then return halt('NATIVE_STATE_UNKNOWN') end
 -- Active branch never consults task messages or their status.
 if c.successor then return out(summary()) end
 if c.generation>=c.policy.max_generations then return halt('GENERATION_COST_BUDGET') end
 c.successor={generation=c.generation+1,notification_id=req.notification_id,state='armed',
  after_receiver_run_id=c.receiver_run_id,created_at=now,request='recheck_bound_producer'}
 return save(summary())
end
if req.action=='publish_acquire' then
 local next=c.successor
 if not next or req.generation~=next.generation or req.notification_id~=next.notification_id or (c.state~='active' and c.state~='between_rounds') then return err('ARMED_SUCCESSOR_REQUIRED') end
 if req.watch_sha256~=c.watch_sha256 then return halt('WATCH_CHANGED') end
 if next.state=='delivered' then return out({status='already_delivered',notification_id=next.notification_id}) end
 if next.state=='sending' or next.state=='unknown' then return out({status='UNKNOWN_QUERY_ONLY',notification_id=next.notification_id}) end
 if next.state~='armed' then return err('SUCCESSOR_NOT_READY') end
 if now>=c.round_deadline then return halt('ROUND_DEADLINE') end
 if now<c.started_at+c.policy.min_interval_seconds then return err('RATE_LIMIT_NO_AUTOMATIC_WAKE') end
 local s=doc.subscriptions[c.subscription_id]
 if not s or s.event_name~='gateway.continuation.ready' or s.expires_at<=now or s.access_until<=now then return halt('SUBSCRIPTION_INACTIVE') end
 c.scope_expires_at=math.min(c.scope_expires_at,req.scope_expires_at,s.expires_at,s.access_until)
 if now>=c.scope_expires_at then return halt('SCOPE_EXPIRED') end
 c.state='between_rounds' -- logical retirement before one callback, not a native-turn completion proof
 next.state='sending';next.lock=req.lock;next.attempts=1;next.started_at=now
 return save({status='acquired',subscription=s,successor=next,binding=c.binding,scope_expires_at=c.scope_expires_at,publish_deadline=math.min(c.scope_expires_at,c.round_deadline)})
end
return err('ACTION_UNKNOWN')
