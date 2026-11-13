/** Execute the patched ORIGINAL DshExecutor methods after Node TypeScript
 * transform. Cordis/session/environment/background dependencies are synthetic
 * recording adapters; this is NOT a production Runtime/native integration. */
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {stripTypeScriptTypes} from 'node:module'
import {SourceTextModule, SyntheticModule} from 'node:vm'
import * as core from './upstream/core/index.ts'
import * as fs from 'node:fs/promises'
import * as path from 'node:path'
class Later extends Error {}
const backgroundCalls = []
const modules = {
  'node:fs/promises':fs, 'node:path':path, '../core/index.ts':core,
  '../infra/environment.ts':{runAsWorker:()=>{throw Error('Unexpected environment effect')}, verifyDirectory:()=>{}},
  '../app/effects.ts':{Later}, '../app/execution-environment.ts':{executionRoot:()=>'/synthetic'},
  '../app/assessment.ts':{WorktreeNameSchema:{parse:x=>x}},
  '../app/notices.ts':{resultRelay:()=>({id:'synthetic',text:'synthetic'})},
  './background.ts':{cancelled:()=>false, collectJob:()=>{},killJobs:()=>{},runningJobs:()=>[],
    stopBackground:async(...args)=>{backgroundCalls.push(args[1])}, stopSubagents:()=>{},until:()=>{},untilQuiet:async()=>true},
}
const module = new SourceTextModule(stripTypeScriptTypes(readFileSync(new URL('../src/executor/dsh.ts',import.meta.url),'utf8'),{mode:'transform'}))
await module.link(async specifier => {
  const values = modules[specifier]
  assert(values, 'Unexpected production dependency '+specifier)
  const exports = Object.keys(values)
  const linked = new SyntheticModule(exports,function(){for(const key of exports)this.setExport(key,values[key])})
  await linked.link(()=>{}); return linked
})
await module.evaluate()
const {DshExecutor} = module.namespace
function fixture(routingEnabled = true) {
  const assignment = {id:'a',state:'pending',createdAt:'2026-11-13T00:00:00Z'}
  const ticket = {id:'t',createdAt:assignment.createdAt,assignments:[assignment],title:'fixture',inputs:[],undelivered:[],
    origin:{triggerId:'synthetic',triggerIds:['synthetic']},execution:{directory:process.cwd(),assignment:''}}
  const logs = [], handlers = {}, commits = [], events = {}
  let allowed = true, claim = 'send', max = 1
  const agent = {id:'worker-t',status:'idle',inbox:{remove:()=>{}},cancel:()=>logs.push('cancel'),whenIdle:async()=>{},
    session:{snapshotEvents:()=>[]}}
  const routing = {check:()=>allowed,claimStart:()=>{logs.push('claim');return claim},recover:()=>logs.push('observe')}
  const host = {routing:routingEnabled?routing:undefined,
    context:{agents:{get:()=>agent},on:(name,fn)=>{events[name]=fn;return ()=>{}}},
    store:{tickets:{get:()=>ticket,open:()=>[ticket],list:()=>[ticket]},triggers:{get:()=>undefined},
      settings:()=>({executor:{maxWorkers:max}}),atomic:fn=>fn(),afterCommit:fn=>commits.push(fn)},
    effects:{handle:(name,fn)=>{handlers[name]=fn},enqueue:()=>logs.push('enqueue')},
    lifetime:new AbortController().signal,health:{get:()=>{},set:()=>{}},machine:{stateDir:'/synthetic'},
    closing:()=>false,paused:()=>false,sourceLink:async()=>{},repliedTo:()=>{},
    engine:Object.fromEntries(['assignmentStarted','inputsDelivered','inputsRead','turnEnded','deliveryFailed','report','assignmentInterrupted'].map(name=>[name,()=>logs.push(name)])),
    sessions:{ensure:async()=>agent,promptedId:id=>id,holds:async()=>false,relay:()=>{},
      prompt:async(_s,_id,_text,_mode,authorize,accepted)=>{authorize?.();logs.push('prompt');accepted?.()}},
  }
  const executor = new DshExecutor(host)
  return {executor,ticket,assignment,host,logs,handlers,commits,events,agent,routing,
    allow:v=>{allowed=v},claim:v=>{claim=v},max:v=>{max=v}}
}

test('original queued launch refuses foreign route before sessions, slots, native fence or reports',async()=>{
  const f=fixture();f.allow(false)
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,[])
})
test('full worker slots leave original once fence untouched',async()=>{
  const f=fixture();f.max(0)
  await assert.rejects(f.handlers.launch({ticketId:'t',assignmentId:'a'}),/No worker slot/)
  assert.deepEqual(f.logs,[])
})
test('repeated native attempt is query-only at actual prompt boundary',async()=>{
  const f=fixture();f.claim('query-only')
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,['claim','observe'])
})
test('original launch calls trusted fence and retains original prompt/started callbacks',async()=>{
  const f=fixture()
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,['claim','prompt','assignmentStarted','assignmentStarted'])
})
test('lost native response observes original attempt without failed report or retry prompt',async()=>{
  const f=fixture();f.host.sessions.prompt=async()=>{f.logs.push('native-unknown');throw Error('ETIMEDOUT')}
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,['claim','native-unknown','observe'])
})
test('revocation while session resolves prevents prompt and started ACK',async()=>{
  const f=fixture();f.host.sessions.ensure=async()=>{f.allow(false);return f.agent}
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,[])
})
test('native authorize callback rechecks changed Source after prompt was requested',async()=>{
  const f=fixture();f.host.sessions.prompt=async(_s,_r,_t,_m,authorize)=>{f.allow(false);authorize();f.logs.push('unsafe-send')}
  await f.handlers.launch({ticketId:'t',assignmentId:'a'})
  assert.deepEqual(f.logs,['claim','observe'])
})
test('original restart is observation only for owned running assignment',()=>{
  const f=fixture();f.assignment.state='running';f.executor.resume()
  assert.deepEqual(f.logs,['observe'])
})
test('original restart leaves foreign assignment and native session untouched',()=>{
  const f=fixture();f.assignment.state='running';f.allow(false);f.executor.resume()
  assert.deepEqual(f.logs,[])
})
test('original delayed stop rechecks ownership after commit',()=>{
  const f=fixture();f.executor.stop(f.ticket,f.assignment,'fixture');f.allow(false)
  f.commits.forEach(fn=>fn());assert.deepEqual(f.logs,[])
})
test('original delivery rechecks route after awaiting stopped work',async()=>{
  const f=fixture();f.assignment.state='running';f.ticket.undelivered=['input']
  f.executor.stopping.set('t',Promise.resolve().then(()=>f.allow(false)))
  await f.handlers.delivery({ticketId:'t'});assert.deepEqual(f.logs,[])
})
test('ordinary unbound executor preserves original enqueue path',()=>{
  const f=fixture(false);f.executor.start(f.ticket,f.assignment)
  assert.deepEqual(f.logs,['enqueue'])
})
test('original shutdown does not cancel or stop background work of foreign route',async()=>{
  const f=fixture();f.allow(false);const before=backgroundCalls.length
  await f.executor.shutdown()
  assert.equal(backgroundCalls.length,before);assert.deepEqual(f.logs,[])
})
test('original shutdown checks latest route instead of adopting a historical owned assignment',async()=>{
  const f=fixture();f.ticket.assignments.push({...f.assignment,id:'replacement'})
  f.routing.check=(_ticket,assignment)=>assignment.id==='a'
  const before=backgroundCalls.length;await f.executor.shutdown()
  assert.equal(backgroundCalls.length,before);assert.deepEqual(f.logs,[])
})
