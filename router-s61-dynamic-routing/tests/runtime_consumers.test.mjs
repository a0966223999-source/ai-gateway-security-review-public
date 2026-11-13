/** Execute patched ORIGINAL Runtime methods. Dependencies are synthetic;
 * this verifies lifecycle composition, not a live host grant. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { SourceTextModule, SyntheticModule } from 'node:vm'
const code = stripTypeScriptTypes(readFileSync(new URL('../src/app/runtime.ts', import.meta.url), 'utf8'), {mode:'transform'})
const imports = new Map()
for (const match of code.matchAll(/import\s+\{([^}]+)\}\s+from\s+['"]([^'"]+)['"]/g)) {
  imports.set(match[2], match[1].split(',').map(name => name.trim().split(/\s+as\s+/)[0]))
}
const module = new SourceTextModule(code)
await module.link(async specifier => {
  const names = imports.get(specifier)
  assert(names, 'Unrecognized dependency ' + specifier)
  const overrides = {Service: class {}, registerHttp: () => () => {},
    health: () => ({state:'paused'}), workerSessionId: id => 'worker-' + id}
  const linked = new SyntheticModule(names, function () {
    for (const name of names) this.setExport(name, overrides[name] ?? (() => {throw Error('Unexpected dependency ' + name)}))
  })
  await linked.link(() => {}); return linked
})
await module.evaluate()
const { Runtime } = module.namespace
function fixture() {
  const log = []
  const record = name => () => {log.push(name)}
  const runtime = Object.assign(Object.create(Runtime.prototype), {
    stopped: new AbortController(), disposers: [], closingFlag:false, machine:{stateDir:'/synthetic'},
    ctx: {agents:{get:()=>undefined},sessionPersistence:{flush:async()=>{log.push('flush')}}},
    setup:{run:async()=>true,restore:record('restore')},
    sessions:{install:record('sessions.install'),ensure:async()=>{},dispose:record('sessions.dispose')},
    executor:{install:record('dsh.install'),resume:record('dsh.resume'),shutdown:async()=>{log.push('dsh.shutdown')},dispose:record('dsh.dispose')},
    settings:{syncable:()=>[]},store:{settings:()=>({}),tickets:{list:()=>[]},close:record('store.close')},
    passes:{start:record('passes.start'),stop:()=>{log.push('passes.stop');return Promise.resolve()}},
    effects:{start:record('effects.start'),drain:async()=>{log.push('effects.drain')}},
    runtimeHealth:{set:record('health')},assessor:{stop:record('assessor.stop')},
    releaseLock:async()=>{log.push('release')},httpHost:()=>({}),
    dynamicExecutor:{install:record('external.install'),recover:record('external.recover'),
      shutdown:async()=>{log.push('external.shutdown')},dispose:record('external.dispose')},
  })
  return {runtime,log}
}
test('original Runtime installs all consumers and recovers before starting loops',async()=>{
  const f=fixture();await f.runtime.initialize()
  assert.deepEqual(f.log,['sessions.install','dsh.install','external.install','external.recover','passes.start','effects.start','health'])
})
test('registration failure prevents recovery and every dispatch loop',async()=>{
  const f=fixture();f.runtime.dynamicExecutor.install=()=>{throw Error('registration failed')}
  await assert.rejects(f.runtime.initialize(),/registration failed/)
  assert.deepEqual(f.log,['sessions.install','dsh.install'])
})
test('ordinary Runtime retains original resume path',async()=>{
  const f=fixture();f.runtime.dynamicExecutor=undefined;await f.runtime.initialize()
  assert(f.log.includes('dsh.resume'));assert(!f.log.includes('external.install'))
})
test('original shutdown keeps Store open until external owner completes',async()=>{
  const f=fixture();let finish
  f.runtime.dynamicExecutor.shutdown=()=>new Promise(resolve=>{finish=resolve})
  const stopping=f.runtime.stop()
  await new Promise(resolve=>setImmediate(resolve))
  assert.equal(f.runtime.lifetime.aborted,true)
  assert(!f.log.includes('store.close'))
  finish();await stopping
  assert(f.log.indexOf('external.dispose')<f.log.indexOf('store.close'))
  assert(f.log.indexOf('store.close')<f.log.indexOf('release'))
})
test('external stop failure remains rejected after DSH cleanup, flush and lock release',async()=>{
  const f=fixture()
  f.runtime.dynamicExecutor.shutdown=async()=>{throw Error('native stop UNKNOWN')}
  await assert.rejects(f.runtime.stop(),/native stop UNKNOWN/)
  for(const name of ['dsh.shutdown','external.dispose','flush','store.close','release'])assert(f.log.includes(name),name)
})
