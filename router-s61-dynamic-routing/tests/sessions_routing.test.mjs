/** ORIGINAL Sessions methods, transformed; all Cordis/SDK adapters below are
 * synthetic. No real model, session, grant or full Runtime is instantiated. */
import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {stripTypeScriptTypes} from 'node:module'
import {SourceTextModule,SyntheticModule} from 'node:vm'
import * as core from './upstream/core/index.ts'
import * as path from 'node:path'
const values={
  '@deepseek-ai/dsh-scope':{scopeParentOf:()=>undefined},'node:path':path,'zod':{z:{toJSONSchema:()=>({})}},
  '../core/index.ts':core,'../executor/dsh.ts':{mandateInstructions:()=>'',ticketOfSession:id=>id.startsWith('worker-')?id.slice(7):undefined},
  './delivery.ts':{AgentDelivery:class{},messageId:id=>id},'./retry-health.ts':{RetryHealth:class{}},
  './role-models.ts':{initializeRoleModel:()=>{}},'./skill-files.ts':{bundledSkills:{}},
  './prompts.ts':{SKILL_PREFIX:'synthetic'},'../kv.ts':{KV:{sessions:{prompts:id=>id}}},
}
const mod=new SourceTextModule(stripTypeScriptTypes(readFileSync(new URL('../src/app/sessions.ts',import.meta.url),'utf8'),{mode:'transform'}))
await mod.link(async name=>{assert(values[name],'Unexpected dependency '+name);const object=values[name]
  const m=new SyntheticModule(Object.keys(object),function(){for(const key of Object.keys(object))this.setExport(key,object[key])})
  await m.link(()=>{});return m})
await mod.evaluate();const {Sessions}=mod.namespace
function fixture(){
  const ticket={id:'t',assignments:[{id:'a',state:'running'}]}, logs=[],tools={}
  let allowed=true
  const host={store:{tickets:{get:()=>ticket}},workerAllowed:()=>allowed,
    prompts:{current:()=>({revision:1})}}
  const s=Object.create(Sessions.prototype);s.host=host
  const agent={id:'worker-t',ctx:{tools:{get:()=>true,restrict:()=>()=>{},guard:fn=>{tools.guard=fn;return ()=>{}},
    register:tool=>{tools.registered=tool;return ()=>{}}}}}
  return {s,host,ticket,agent,tools,logs,allow:v=>{allowed=v}}
}
test('original session ticket lookup cannot adopt another executor or revoked Source',()=>{
  const f=fixture();assert.equal(f.s.ticketOf(f.agent),f.ticket);f.allow(false)
  assert.equal(f.s.ticketOf(f.agent),undefined)
})
test('original inherited tool guard rechecks current ownership at each invocation',()=>{
  const f=fixture();f.s.workOnly(f.agent,'t');assert.equal(f.tools.guard(),undefined)
  f.allow(false);assert.match(f.tools.guard(),/route or Source changed/)
})
test('already registered original worker tool refuses after route revocation',()=>{
  const f=fixture();const binding={disposers:[]}
  f.s.registerTool(f.agent,binding)('synthetic','fixture',{parse:x=>x},()=>f.logs.push('effect'))
  f.allow(false)
  assert.throws(()=>f.tools.registered.execute({}, {signal:new AbortController().signal}),/ROUTE_OR_SOURCE_CHANGED/)
  assert.deepEqual(f.logs,[])
})
test('original model prompt binding refuses changed Source before prompt revision writes',()=>{
  const f=fixture();f.allow(false)
  assert.throws(()=>f.s.promptFor(f.agent,true),/ROUTE_OR_SOURCE_CHANGED/)
})
test('original session creation refuses foreign route before resolving native agent',async()=>{
  const f=fixture();f.allow(false);f.s.ctx={sessionController:{resolveAgent:()=>f.logs.push('resolve')}}
  await assert.rejects(f.s.ensure('worker-t','/synthetic','fixture','worker'),/ROUTE_OR_SOURCE_CHANGED/)
  assert.deepEqual(f.logs,[])
})
