import test from 'node:test';
import assert from 'node:assert/strict';
import {createReceiveIdentityGate} from './receive_identity_gate.mjs';

// All identities/receipts below are OFFLINE FIXTURES, never live attestations.
const scope = {mailboxId:'gateway_internal_v1', receiverThreadId:'receiver',
  senderThreadIds:['sender'], taskId:'task'};
const event = {event_id:'event', operation_key:'operation',
  mailbox_id:'gateway_internal_v1', payload:{from_thread_id:'sender',
    to_thread_id:'receiver', task_id:'task'}};
const page = (items = [], cursor = null) => ({structuredContent:{
  threadId:'sender', latestTurn:{id:'turn',status:'completed',error:null},
  items, nextCursor:cursor}});
const proof = {status:'VERIFIED',eventId:'event',operationKey:'operation',
  senderThreadId:'sender',receiverThreadId:'receiver',taskId:'task',
  nativeSendReceiptId:'OFFLINE-ONLY'};
function fixture(overrides = {}, enabled = true) {
  const calls = {read:[],process:0,verify:0};
  const host = {current:()=>true,authorize:async()=>true,
    readThread:async args=>{calls.read.push(args);return page();},
    verifyNativeMailEvidence:async()=>{calls.verify++;return proof;},
    originalReceive:async(e,context)=>{
      if(context && !await context.revalidate()) return {status:'REVOKED'};
      calls.process++;return {status:'ORIGINAL_RECEIVER_CALLED'};
    }, ...overrides};
  return {gate:createReceiveIdentityGate({host,scope,enabled}),calls,host};
}
test('disabled by default: original receiver untouched, no read',async()=>{
  const f=fixture({},false);await f.gate.receive(event);
  assert.equal(f.calls.process,1);assert.equal(f.calls.read.length,0);
});
test('idle construction and status never read a thread',()=>{
  const f=fixture();assert.equal(f.gate.status().pending,0);
  assert.equal(f.calls.read.length,0);
});
test('targeted read and exact host evidence reach original receiver',async()=>{
  const f=fixture();assert.equal((await f.gate.receive(event)).status,'ORIGINAL_RECEIVER_CALLED');
  assert.deepEqual(f.calls.read,[{threadId:'sender',limit:20}]);
});
test('missing real native reader holds without processing',async()=>{
  const f=fixture({readThread:undefined});
  assert.match((await f.gate.receive(event)).reason,/NOT_BOUND/);
  assert.equal(f.calls.process,0);
});
test('mere thread existence is insufficient',async()=>{
  const f=fixture({verifyNativeMailEvidence:async()=>null});
  assert.equal((await f.gate.receive(event)).reason,'EXACT_NATIVE_SEND_NOT_VERIFIED');
  assert.equal(f.calls.process,0);
});
test('wrong receipt event cannot authorize a letter',async()=>{
  const f=fixture({verifyNativeMailEvidence:async()=>({...proof,eventId:'other'})});
  assert.equal((await f.gate.receive(event)).reason,'EXACT_NATIVE_SEND_NOT_VERIFIED');
});
test('foreign target and mailbox rejected before reading',async()=>{
  for(const e of [{...event,mailbox_id:'other'},
    {...event,payload:{...event.payload,to_thread_id:'other'}}]) {
    const f=fixture();assert.equal((await f.gate.receive(e)).reason,'EVENT_OUTSIDE_PINNED_SCOPE');
    assert.equal(f.calls.read.length,0);
  }
});
test('FORBIDDEN is query-only, no retry or processing',async()=>{
  let reads=0;const f=fixture({readThread:async()=>{reads++;throw Error('FORBIDDEN');}});
  assert.match((await f.gate.receive(event)).reason,/UNKNOWN_QUERY_ONLY/);
  assert.equal(reads,1);assert.equal(f.calls.process,0);
});
test('all pages captured without environment override',async()=>{
  const f=fixture({readThread:async args=>{f.calls.read.push(args);
    return args.cursor?page([{id:'b',truncated:false}]):page([{id:'a',truncated:false}],'next');},
    verifyNativeMailEvidence:async({capture})=>{
      assert.deepEqual(capture.items.map(x=>x.id),['a','b']);return proof;}});
  await f.gate.receive(event);assert.equal(f.calls.process,1);
  assert.deepEqual(f.calls.read[1],{threadId:'sender',limit:20,cursor:'next'});
});
test('cursor cycles block',async()=>{
  const f=fixture({readThread:async()=>page([],'loop')});
  assert.equal((await f.gate.receive(event)).reason,'NATIVE_CURSOR_INVALID_OR_CYCLE');
  assert.equal(f.calls.process,0);
});
test('revocation after read blocks processing',async()=>{
  let live=true;const f=fixture({current:()=>live,
    readThread:async()=>{live=false;return page();}});
  assert.equal((await f.gate.receive(event)).reason,'AUTHORIZATION_CHANGED');
  assert.equal(f.calls.process,0);
});
test('rollback cancels an in-flight verification then restores original path',async()=>{
  let release;const f=fixture({readThread:()=>new Promise(r=>{release=r;})});
  const run=f.gate.receive(event);
  while(!release) await new Promise(r=>setImmediate(r));
  f.gate.rollback();release(page());
  assert.equal((await run).reason,'AUTHORIZATION_CHANGED');assert.equal(f.calls.process,0);
  await f.gate.receive(event);assert.equal(f.calls.process,1);
});
test('concurrent same event never enters receiver twice',async()=>{
  let release;const f=fixture({readThread:()=>new Promise(r=>{release=r;})});
  const first=f.gate.receive(event);
  while(!release) await new Promise(r=>setImmediate(r));
  assert.equal((await f.gate.receive(event)).reason,'SAME_EVENT_ALREADY_IN_PROGRESS');
  release(page());await first;assert.equal(f.calls.process,1);
});
test('truncated items never become evidence',async()=>{
  const f=fixture({readThread:async()=>page([{id:'a',truncated:true}])});
  assert.equal((await f.gate.receive(event)).reason,'NATIVE_ITEMS_INCOMPLETE_OR_DUPLICATED');
  assert.equal(f.calls.verify,0);
});
test('original receiver exception preserves unknown side effects',async()=>{
  const f=fixture({originalReceive:async()=>{throw Error('saved but reply lost');}});
  const r=await f.gate.receive(event);
  assert.equal(r.status,'PROCESSING_OUTCOME_UNKNOWN_QUERY_ONLY');
  assert.equal(r.processing,'UNKNOWN');assert.equal(r.transportAck,'UNKNOWN');
});
