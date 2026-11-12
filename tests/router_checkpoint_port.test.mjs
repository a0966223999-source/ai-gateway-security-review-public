import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRouterCheckpointPort} from '../router-s61-restored/router_checkpoint_port.mjs';

// Explicit offline transport responses, not native authority or live acceptance.
const source='a'.repeat(64), revision='b'.repeat(64), nextRevision='c'.repeat(64);
const scope={link_id:'d'.repeat(64),thread_id:'offline-test-thread'};
const names={read:'mcp__codex_apps__x_redis_test_gateway_checkpoint_read',
 save:'mcp__codex_apps__x_redis_test_gateway_checkpoint_save',
 receipt:'mcp__codex_apps__x_redis_test_gateway_checkpoint_receipt'};
const checkpoint={status:'IN_PROGRESS',next_step:'resume original work',test_id:'offline'};
function response(extra={}) {
 return {status:'CHECKPOINT_RESULT_VERIFIED',source_key:source,revision,
  checkpoint,expires_at:2000,scope:'platform-checkpoint-only',native_work_authority:false,...extra};
}
function fixture(handlers={}) {
 const calls=[];
 const tools=Object.fromEntries(Object.entries(names).map(([kind,name])=>[name,async arg=>{
  calls.push({kind,arg:structuredClone(arg)});
  return (handlers[kind]||(()=>response()))(arg);
 }]));
 return {calls,tools,port:createRouterCheckpointPort({tools,scope,clock:()=>1000})};
}
test('construction uses no tools and rejects missing capabilities or extra scope authority',()=>{
 const f=fixture();assert.equal(f.calls.length,0);
 assert.throws(()=>createRouterCheckpointPort({tools:{},scope}),/TOOL_NOT_FOUND/);
 assert.throws(()=>createRouterCheckpointPort({tools:f.tools,scope:{...scope,owner:'admin'}}),/SCOPE_REQUIRED/);
});
test('read decodes actual MCP text envelope and preserves checkpoint-only classification',async()=>{
 const f=fixture({read:()=>({content:[{type:'text',text:JSON.stringify(response())}],structuredContent:null})});
 const r=await f.port.read({operation_key:'read-1',source_key:source});
 assert.equal(r.status,'CHECKPOINT_READY');assert.equal(r.dispatch_allowed,false);
 assert.equal(r.native_completion_verified,false);assert.equal(r.transport_ack_allowed,false);
 assert.deepEqual(f.calls[0].arg.request,{...scope,operation_key:'read-1',source_key:source});
});
test('historical or payloadless receipts cannot become current observations',async()=>{
 for(const extra of [{historical_only:true},{authority:false}]) {
  const f=fixture({read:()=>response(extra)});
  const r=await f.port.read({operation_key:'old',source_key:source});
  assert.equal(r.status,'QUERY_ORIGINAL_ONLY');
 }
 const f=fixture({read:()=>{const value=response();delete value.checkpoint;return value}});
 assert.equal((await f.port.read({operation_key:'missing-payload',source_key:source})).status,'QUERY_ORIGINAL_ONLY');
});
test('expired, wrong Source, elevated scope and malformed replies fail closed',async()=>{
 for(const extra of [{expires_at:999},{source_key:'f'.repeat(64)},
  {scope:'native-dispatch'},{native_work_authority:true},{revision:'not-a-revision'}]) {
  const f=fixture({read:()=>response(extra)});
  await assert.rejects(f.port.read({operation_key:'read',source_key:source}),/LIVE_SCOPE_OR_CONTENT_INVALID/);
 }
});
test('save requires issued observation and uses original CAS binding',async()=>{
 const f=fixture({save:({request})=>response({revision:nextRevision,checkpoint:request.checkpoint})});
 await assert.rejects(f.port.save({operation_key:'save',observation:{},checkpoint}),/ISSUED/);
 const before=await f.port.read({operation_key:'before',source_key:source});
 const saved=await f.port.save({operation_key:'save',observation:before,checkpoint:{...checkpoint,result:'saved'}});
 assert.equal(saved.independent_read_required,true);
 assert.equal(f.calls[1].arg.request.expected_revision,revision);
 assert.equal(f.calls[1].arg.request.source_key,source);
 assert.equal(saved.transport_ack_allowed,false);
});
test('concurrent UNKNOWN save is attempted once and query never resubmits',async()=>{
 const f=fixture({save:()=>{throw Error('transport interrupted')},receipt:()=>response({historical_only:true})});
 const observation=await f.port.read({operation_key:'read',source_key:source});
 const request={operation_key:'save-once',observation,checkpoint};
 const replies=await Promise.all([f.port.save(request),f.port.save(request)]);
 assert.ok(replies.every(r=>r.status==='QUERY_ORIGINAL_ONLY'));
 await f.port.query({operation_key:'save-once',operation_kind:'execute'});
 assert.equal(f.calls.filter(x=>x.kind==='save').length,1);
 assert.equal(f.calls.filter(x=>x.kind==='receipt').length,1);
});
test('confirmed save requires a separate full read, no turn IDs or fabricated ACK',async()=>{
 let saved=false;
 const updated={...checkpoint,result:'verified'};
 const f=fixture({read:()=>response(saved?{revision:nextRevision,checkpoint:updated}:{}),
  save:()=>{saved=true;return response({revision:nextRevision,checkpoint:updated})}});
 const observation=await f.port.read({operation_key:'before',source_key:source});
 const result=await f.port.save({operation_key:'save',observation,checkpoint:updated});
 await assert.rejects(f.port.confirm({saved:result,operation_key:'save'}),/INDEPENDENT_READ_KEY/);
 const verified=await f.port.confirm({saved:result,operation_key:'independent-read'});
 assert.equal(verified.status,'CHECKPOINT_RESULT_READBACK_VERIFIED');
 assert.equal(verified.receiver_result_check_required,true);
 assert.equal(verified.transport_ack_allowed,false);
});
test('save reply extension/content mismatch and changed independent read are rejected',async()=>{
 const f=fixture({save:()=>response({revision:nextRevision,expires_at:2001})});
 const observation=await f.port.read({operation_key:'before',source_key:source});
 await assert.rejects(f.port.save({operation_key:'save',observation,checkpoint}),/SAVE_MISMATCH/);
 const g=fixture({save:()=>response({revision:nextRevision})});
 const before=await g.port.read({operation_key:'before',source_key:source});
 const saved=await g.port.save({operation_key:'save',observation:before,checkpoint});
 await assert.rejects(g.port.confirm({saved,operation_key:'after'}),/READBACK_MISMATCH/);
});
test('tool reference/scope capture cannot be rerouted after construction',async()=>{
 const f=fixture();f.tools[names.read]=()=>{throw Error('replaced tool used')};
 const r=await f.port.read({operation_key:'read',source_key:source});
 assert.equal(r.status,'CHECKPOINT_READY');assert.equal(f.calls.length,1);
});
