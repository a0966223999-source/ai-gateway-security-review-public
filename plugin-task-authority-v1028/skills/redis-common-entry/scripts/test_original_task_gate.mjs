import test from 'node:test';
import assert from 'node:assert/strict';
import {createReceiveIdentityGate} from './receive_identity_gate.mjs';

// OFFLINE CONTRACT FIXTURES: these do not supply a live grant or reader.
const scope = {mailboxId:'gateway_internal_v1', ownerId:'fixture-owner',
  sourceRef:'fixture-source', taskId:'fixture-task', receiverThreadId:'receiver'};
const event = {mailbox_id:scope.mailboxId,event_id:'fixture-event',operation_key:'fixture-op',
  payload:{task_id:scope.taskId,to_thread_id:'receiver',from_thread_id:'unverified',
    next_step:'FORGED_ACTION',verificationMode:'native_sender'}};
const task = () => ({status:'AUTHORIZED',ownerId:scope.ownerId,sourceRef:scope.sourceRef,
  taskId:scope.taskId,receiverThreadId:'receiver',eventId:event.event_id,
  operationKey:event.operation_key,revision:'opaque-revision',revoked:false,
  actionKind:'continue_original_task',requiresSenderIdentity:false,
  expiresAt:Date.now()/1000+300,nextStep:'ORIGINAL_NEXT_STEP'});
function fixture(overrides={}) {
  const original = task(); let effects=0,reads=0;
  const host = {current:()=>true,resolveOriginalTask:async args=>{
    reads++;assert.equal('payload' in args,false);return structuredClone(original);},
    originalReceive:async(e,c)=>{
      assert.equal(c.nativeSenderVerified,false);
      assert.equal(c.originalTask.nextStep,'ORIGINAL_NEXT_STEP');
      if(!await c.revalidate()) return {status:'REVOKED'};
      effects++;return {status:'PROCESSED'};
    },...overrides};
  const gate=createReceiveIdentityGate({host,scope,enabled:true,verificationMode:'original_task'});
  return {gate,host,original,effects:()=>effects,reads:()=>reads};
}
test('original task continues with no native reader or sender identity',async()=>{
  const f=fixture();assert.equal((await f.gate.receive(event)).status,'PROCESSED');
  assert.equal(f.effects(),1);assert.equal(f.reads(),3);
});
test('payload task/owner/instruction cannot replace original task',async()=>{
  const f=fixture();await f.gate.receive({...event,payload:{...event.payload,
    ownerId:'attacker',nextStep:'DELETE',requiresSenderIdentity:false}});
  assert.equal(f.effects(),1);
});
test('no original reader holds; payload authorization cannot help',async()=>{
  const f=fixture({resolveOriginalTask:undefined});
  assert.equal((await f.gate.receive(event)).reason,'NOT_BOUND_ORIGINAL_TASK_READER');
  assert.equal(f.effects(),0);
});
test('bare true is not original task authority',async()=>{
  const f=fixture({resolveOriginalTask:async()=>true});
  assert.equal((await f.gate.receive(event)).reason,'ORIGINAL_TASK_AUTHORIZATION_REQUIRED');
});
test('wrong task source owner recipient event or operation cannot authorize',async()=>{
  for(const key of ['taskId','sourceRef','ownerId','receiverThreadId','eventId','operationKey']) {
    const f=fixture();f.original[key]='other';
    assert.equal((await f.gate.receive(event)).reason,'ORIGINAL_TASK_AUTHORIZATION_REQUIRED');
    assert.equal(f.effects(),0);
  }
});
test('expired revoked identity-specific and non-continuation actions held',async()=>{
  for(const patch of [{expiresAt:0},{revoked:true},{requiresSenderIdentity:true},
    {actionKind:'approve_new_task'},{status:'UNKNOWN'}]) {
    const f=fixture();Object.assign(f.original,patch);
    assert.equal((await f.gate.receive(event)).reason,'ORIGINAL_TASK_AUTHORIZATION_REQUIRED');
    assert.equal(f.effects(),0);
  }
});
test('source revision changes during read: no effect',async()=>{
  let n=0;const t=task();const f=fixture({resolveOriginalTask:async()=>({...t,revision:String(++n)})});
  assert.equal((await f.gate.receive(event)).reason,'TASK_CHANGED_QUERY_ONLY');
  assert.equal(f.effects(),0);
});
test('revocation at receiver effect boundary blocks effect',async()=>{
  const t=task();let n=0;const f=fixture({resolveOriginalTask:async()=>({...t,revoked:++n>=3})});
  assert.equal((await f.gate.receive(event)).status,'REVOKED');assert.equal(f.effects(),0);
});
test('read failure is query-only and no alternate native read attempted',async()=>{
  let n=0;const f=fixture({resolveOriginalTask:async()=>{n++;throw Error('FORBIDDEN');},
    readThread:()=>{throw Error('MUST_NOT_READ_CHAT');}});
  assert.equal((await f.gate.receive(event)).reason,'ORIGINAL_TASK_READ_UNKNOWN_QUERY_ONLY');
  assert.equal(n,1);assert.equal(f.effects(),0);
});
test('processing lost response preserves UNKNOWN without sender claim',async()=>{
  const f=fixture({originalReceive:async()=>{throw Error('lost after save');}});
  const r=await f.gate.receive(event);assert.equal(r.processing,'UNKNOWN');
  assert.equal(r.nativeSenderVerified,false);assert.equal(r.transportAck,'UNKNOWN');
});
test('rollback invalidates a pending task check',async()=>{
  let release;const f=fixture({resolveOriginalTask:()=>new Promise(r=>{release=r;})});
  const run=f.gate.receive(event);while(!release)await new Promise(r=>setImmediate(r));
  f.gate.rollback();release(task());await run;assert.equal(f.effects(),0);
});
test('same-event overlap is held without duplicate processing',async()=>{
  let release;const t=task();const f=fixture({resolveOriginalTask:()=>new Promise(r=>{release=()=>r(t);})});
  const run=f.gate.receive(event);while(!release)await new Promise(r=>setImmediate(r));
  assert.equal((await f.gate.receive(event)).reason,'SAME_EVENT_ALREADY_IN_PROGRESS');
  f.gate.rollback();release();await run;assert.equal(f.effects(),0);
});
test('mode cannot be selected by payload or misspelling',()=>{
  assert.throws(()=>createReceiveIdentityGate({host:{originalReceive:()=>{}},scope,
    verificationMode:'allow'}),/HOST_VERIFICATION_MODE_REQUIRED/);
});
