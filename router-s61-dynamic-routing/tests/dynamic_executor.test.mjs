/** Original S61 Engine/MemoryRepositories + real SQLite KV/queue CONTRACT fixture.
 * Identity, requirements and provider adapters are synthetic; no live grant,
 * native send, model, original production Store or production Effects is used.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { DatabaseSync } from 'node:sqlite'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Engine } from './upstream/core/engine.ts'
import { MemoryRepositories } from './upstream/core/memory.ts'
import { bindDynamicExecutor, bindRuntimeDynamicExecutor } from '../src/executor/dynamic_executor.mjs'

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 's61-route-contract-'))
  const path = join(directory, 'fixture.sqlite')
  let db = new DatabaseSync(path)
  db.exec('CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE effects(provider TEXT,action TEXT,ticket TEXT,assignment TEXT)')
  const repo = new MemoryRepositories()
  const originalAtomic = repo.atomic.bind(repo)
  let depth = 0
  const store = Object.assign(repo, {
    atomic(work) {
      return originalAtomic(() => {
        const name = 'route_test_' + depth++
        db.exec('SAVEPOINT ' + name)
        try { const value = work(); db.exec('RELEASE ' + name); return value }
        catch (error) { db.exec('ROLLBACK TO ' + name); db.exec('RELEASE ' + name); throw error }
        finally { depth-- }
      })
    },
    get(key) { const row = db.prepare('SELECT value FROM kv WHERE key=?').get(key); return row && JSON.parse(row.value) },
    set(key, value) { db.prepare('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, JSON.stringify(value)) },
  })
  let revision = 'policy-v1', paused = false, authorized = true, failQueue = false
  let required = { capabilities: ['read'], sourceRevision: 'source-v1', sourceDigest: 'a'.repeat(64) }
  const policy = { get version() { return revision }, get paused() { return paused },
    revalidate: () => authorized, gate: () => ({ admissible: true, strength: 'explicit', basis: { via: 'listed' } }),
    admitsInferred: () => false, evalTimeoutMs: 60000 }
  const availability = { general: true, specialist: true }
  let checked = () => true
  const providers = ['general', 'specialist'].map((id, index) => ({
    id, revision: 'adapter-v1', store, capabilities: index ? ['read', 'specialist-role'] : ['read'],
    priority: index ? 10 : 20, available: () => availability[id],
    executor: Object.fromEntries(['start', 'deliver', 'stop'].map(action => [action, (ticket, assignment) => {
      db.prepare('INSERT INTO effects VALUES (?,?,?,?)').run(id, action, ticket.id, assignment.id)
      if (failQueue) throw new Error('synthetic queue error')
    }])),
  }))
  const options = { store, providers, policy: () => policy,
    readRequirements: () => required, checkCurrent: args => checked(args) }
  let executor = bindDynamicExecutor(options)
  const engine = new Engine({ repo: store, executor, policy: () => policy,
    scope: { scope: () => ({ listened: true, observed: false }) },
    assessor: { request() {} }, outbox: { send() {}, status() {} },
    now: () => '2026-11-13T00:00:00.000Z' })
  let sequence = 0
  function create() {
    const id = 'synthetic-trigger-' + sequence++
    engine.receive({ id, source: 'synthetic', threads: [{ source: 'synthetic', id }],
      actor: { source: 'synthetic', id: 'fixture-owner' }, human: true, addressed: true,
      at: '2026-11-13T00:00:00.000Z', text: 'Synthetic task; not live authority', meta: {} })
    const decision = engine.decide(id, { kind: 'accept', title: 'synthetic', scope: 'synthetic', reason: 'fixture' })
    const ticket = store.tickets.list().find(ticket => ticket.decisionId === decision.id)
    return { ticket, assignment: ticket.assignments.at(-1) }
  }
  return { store, providers, options, availability, engine, create,
    get executor() { return executor },
    requirements(value) { required = value }, get requirementsNow() { return required },
    policyVersion(value) { revision = value }, paused(value) { paused = value },
    authorized(value) { authorized = value }, queueFailure(value) { failQueue = value },
    checker(fn) { checked = fn },
    effects: () => db.prepare('SELECT * FROM effects').all(),
    records: () => db.prepare('SELECT * FROM kv').all(),
    reopen() { db.close(); db = new DatabaseSync(path); executor = bindDynamicExecutor(options) },
    close() { db.close(); rmSync(directory, { recursive: true, force: true }) },
  }
}

test('original Engine dynamically routes capability requirements to two executor adapters', () => {
  const f = fixture()
  try {
    f.create()
    f.requirements({ ...f.requirementsNow, capabilities: ['specialist-role'] })
    f.create()
    assert.deepEqual(f.effects().map(row => row.provider), ['general', 'specialist'])
    assert.equal(f.records().length, 2)
    assert(f.records().every(row => JSON.parse(row.value).completionVerified === false))
  } finally { f.close() }
})

test('unavailable higher priority executor is excluded before any dispatch', () => {
  const f = fixture()
  try { f.availability.general = false; f.create(); assert.equal(f.effects()[0].provider, 'specialist') }
  finally { f.close() }
})

test('required provider never silently falls back', () => {
  const f = fixture()
  try {
    f.requirements({ ...f.requirementsNow, requiredProvider: 'general' })
    f.availability.general = false
    assert.throws(() => f.create(), /NO_AUTHORIZED_AVAILABLE_ROUTE/)
    assert.equal(f.effects().length, 0)
    assert.equal(f.records().length, 0)
    assert.equal(f.store.tickets.list().length, 0)
  } finally { f.close() }
})

test('missing capability refuses with no original Engine ticket or queue mutation', () => {
  const f = fixture()
  try {
    f.requirements({ ...f.requirementsNow, capabilities: ['native-work-unbound'] })
    assert.throws(() => f.create(), /NO_AUTHORIZED_AVAILABLE_ROUTE/)
    assert.equal(f.effects().length, 0)
    assert.equal(f.store.tickets.list().length, 0)
  } finally { f.close() }
})

test('queue error rolls selection and original transaction effects back together', () => {
  const f = fixture()
  try {
    f.queueFailure(true)
    assert.throws(() => f.create(), /synthetic queue error/)
    assert.equal(f.effects().length, 0)
    assert.equal(f.records().length, 0)
    assert.equal(f.store.tickets.list().length, 0)
  } finally { f.close() }
})

test('same assignment is query-only after SQLite reopen; no reselect or enqueue', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    f.reopen(); f.availability.general = false
    assert.equal(f.executor.start(ticket, assignment).status, 'QUERY_ONLY_ALREADY_QUEUED')
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('preview performs no writes and cannot grant authority', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    const rows = f.records().length, count = f.effects().length
    assert.equal(f.executor.preview(ticket.id, assignment.id).authority, false)
    assert.equal(f.records().length, rows)
    assert.equal(f.effects().length, count)
  } finally { f.close() }
})

test('delivery remains with chosen executor even when availability preferences change', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    f.engine.assignmentStarted(ticket.id, assignment.id)
    f.availability.general = false
    f.executor.deliver(ticket, assignment)
    assert.deepEqual(f.effects().map(row => row.provider), ['general', 'general'])
  } finally { f.close() }
})

test('policy revision change refuses delivery instead of changing route', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    f.policyVersion('policy-v2')
    assert.throws(() => f.executor.deliver(ticket, assignment), /RECORDED_SOURCE_CHANGED/)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('stop preserves original owner/provider after revocation and deduplicates', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    f.authorized(false); f.paused(true); f.availability.general = false
    assert.equal(f.executor.stop(ticket, assignment, 'owner stop').providerId, 'general')
    assert.equal(f.executor.stop(ticket, assignment, 'owner stop').status, 'QUERY_ONLY_STOP_ALREADY_QUEUED')
    assert.deepEqual(f.effects().map(row => row.action), ['start', 'stop'])
  } finally { f.close() }
})

test('stop cannot touch a different assignment or bypass retained ownership', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    assert.throws(() => f.executor.stop(ticket, { id: 'foreign' }, 'stop'), /CURRENT_ORIGINAL_ASSIGNMENT/)
    f.checker(() => false)
    assert.throws(() => f.executor.stop(ticket, assignment, 'stop'), /STOP_OWNERSHIP_DENIED/)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('source changes during candidate inspection prevent all enqueue effects', () => {
  const f = fixture()
  try {
    f.providers[0].available = () => { f.requirements({ ...f.requirementsNow, sourceRevision: 'changed' }); return true }
    // A newly bound registry captures these trusted fixture callbacks.
    const router = bindDynamicExecutor(f.options)
    const { ticket, assignment } = f.create()
    const second = { ...assignment, id: 'another-assignment' }
    f.store.tickets.save({ ...ticket, assignments: [second] })
    assert.throws(() => router.start(ticket, second), /CONTEXT_CHANGED/)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('unknown health and truthy source checks never count as permission', () => {
  const f = fixture()
  try {
    f.checker(() => 'true')
    assert.throws(() => f.create(), /CURRENT_SOURCE_DENIED/)
    f.checker(() => true)
    f.providers[0].available = () => 'true'
    const router = bindDynamicExecutor(f.options)
    const { ticket, assignment } = f.create()
    assert.throws(() => router.preview(ticket.id, assignment.id), /PROVIDER_STATUS_UNKNOWN/)
  } finally { f.close() }
})

test('provider from another Store cannot be registered', () => {
  const f = fixture()
  try {
    assert.throws(() => bindDynamicExecutor({ ...f.options,
      providers: [{ ...f.providers[0], store: {} }] }), /ORIGINAL_QUEUE_PROVIDER_REQUIRED/)
  } finally { f.close() }
})

test('ordinary Runtime composition returns original executor with zero registry or source calls', () => {
  const originalExecutor = Object.freeze({})
  assert.equal(bindRuntimeDynamicExecutor({ originalExecutor }), originalExecutor)
})

test('JSON routing config is rejected before any provider factory invocation', () => {
  assert.throws(() => bindRuntimeDynamicExecutor({ capability: { providers: [] },
    lifetime: new AbortController().signal }), /HOST_CAPABILITY_NOT_BOUND/)
})

test('wrong runtime lifetime rejects adapters before queue writes', () => {
  const f = fixture()
  try {
    const signal = new AbortController().signal
    assert.throws(() => bindRuntimeDynamicExecutor({ store: f.store, lifetime: signal,
      policy: f.options.policy, originalExecutor: f.providers[0].executor,
      capability: { ...f.options, providers: () => f.providers.map(p => ({ ...p,
        lifetime: new AbortController().signal })) } }), /LIFETIME_MISMATCH/)
    assert.equal(f.effects().length, 0)
  } finally { f.close() }
})

test('original Engine stop callback targets retained executor after ticket ended', () => {
  const f = fixture()
  try {
    const { ticket } = f.create()
    f.engine.stopTicket(ticket.id, 'fixture owner stop')
    assert.deepEqual(f.effects().map(row => row.action), ['start', 'stop'])
    assert.equal(f.effects()[1].provider, 'general')
  } finally { f.close() }
})

test('malformed durable route record cannot authorize delivery or stop', () => {
  const f = fixture()
  try {
    const { ticket, assignment } = f.create()
    const row = f.records()[0]
    f.store.set(row.key, { ...JSON.parse(row.value), schema: 'forged' })
    assert.throws(() => f.executor.stop(ticket, assignment, 'stop'), /ORIGINAL_ROUTE_NOT_BOUND/)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('lifecycle ownership rejects foreign providers and changed Source at actual launch', () => {
  const f = fixture()
  try {
    const {ticket, assignment} = f.create()
    assert.equal(f.executor.lifecycle('specialist').check(ticket, assignment, 'launch'), false)
    assert.equal(f.executor.lifecycle('general').check(ticket, assignment, 'launch'), true)
    f.requirements({...f.requirementsNow, sourceRevision:'revoked'})
    assert.equal(f.executor.lifecycle('general').check(ticket, assignment, 'launch'), false)
    assert.equal(f.executor.lifecycle('general').check(ticket, assignment, 'stop'), true)
  } finally { f.close() }
})

test('original native once fence outcome stays query-only on repeated claim', () => {
  const f = fixture()
  try {
    let used = false
    f.providers[0].claimStart = () => used ? 'query-only' : (used = true, 'send')
    const router = bindDynamicExecutor(f.options)
    const {ticket, assignment} = f.create()
    assert.equal(router.lifecycle('general').claimStart(ticket, assignment), 'send')
    assert.equal(router.lifecycle('general').claimStart(ticket, assignment), 'query-only')
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('native UNKNOWN and missing native once fence refuse sending', () => {
  const f = fixture()
  try {
    const {ticket, assignment} = f.create()
    assert.throws(() => f.executor.lifecycle('general').claimStart(ticket, assignment), /NATIVE_FENCE_NOT_BOUND/)
    f.providers[0].claimStart = () => 'UNKNOWN'
    assert.throws(() => bindDynamicExecutor(f.options).lifecycle('general').claimStart(ticket, assignment), /OUTCOME_UNKNOWN/)
  } finally { f.close() }
})

test('revocation inside original native fence is rechecked before send', () => {
  const f = fixture()
  try {
    f.providers[0].claimStart = () => { f.authorized(false); return 'send' }
    const router = bindDynamicExecutor(f.options), {ticket, assignment} = f.create()
    assert.throws(() => router.lifecycle('general').claimStart(ticket, assignment), /PAUSED_OR_REVOKED/)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('recovery reads saved provider once without enqueue or changing policy grant', () => {
  const f = fixture()
  try {
    let observed = 0
    f.providers[0].recover = () => { observed++ }
    const router = bindDynamicExecutor(f.options)
    f.create(); f.authorized(false); f.paused(true)
    router.recover()
    assert.equal(observed, 1)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

function runtimeOptions(f, signal) {
  return { store:f.store, policy:f.options.policy, lifetime:signal, originalExecutor:f.providers[0].executor,
    capability:{ originalProviderId:'general', readRequirements:f.options.readRequirements, checkCurrent:f.options.checkCurrent,
      providers:() => f.providers.map(p => ({...p, lifetime:signal, claimStart:()=>'query-only', recover:()=>{}, attachLifecycle:()=>{}})) } }
}
test('trusted Runtime binding attaches external lifecycle without enqueue; abort denies launch but permits cleanup', () => {
  const f = fixture()
  try {
    const controller = new AbortController(), options = runtimeOptions(f, controller.signal)
    const router = bindRuntimeDynamicExecutor(options)
    const {ticket, assignment} = f.create()
    assert.equal(router.lifecycle('general').check(ticket, assignment, 'launch'), true)
    controller.abort()
    assert.equal(router.lifecycle('general').check(ticket, assignment, 'launch'), false)
    assert.equal(router.lifecycle('general').check(ticket, assignment, 'shutdown'), true)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('Runtime refuses legacy active assignment with no saved route before attaching providers', () => {
  const f = fixture()
  try {
    f.create()
    for (const row of f.records()) f.store.set(row.key, null)
    let attached = 0
    const options = runtimeOptions(f, new AbortController().signal)
    const descriptors = options.capability.providers()
    descriptors.forEach(p => {p.attachLifecycle = () => {attached++}})
    options.capability.providers = () => descriptors
    assert.throws(() => bindRuntimeDynamicExecutor(options), /RECOVERY_ROUTE_MISSING/)
    assert.equal(attached, 0)
    assert.equal(f.effects().length, 1)
  } finally { f.close() }
})

test('old assignment remains stoppable when original Engine has already stored its replacement', () => {
  const f = fixture()
  try {
    const {ticket, assignment} = f.create()
    const old = {...assignment, state:'ended', endedReason:'retried'}, next = {...assignment, id:'new-assignment'}
    const updated = {...ticket, assignments:[old,next]}; f.store.tickets.save(updated)
    f.executor.stop(updated, old, 'retried')
    assert.equal(f.effects().at(-1).assignment, assignment.id)
    assert.equal(f.executor.lifecycle('general').check(updated, old, 'stop'), true)
    assert.equal(f.executor.lifecycle('general').check(updated, old, 'launch'), false)
  } finally { f.close() }
})

test('queued Stop blocks subsequent launch and delivery even while assignment remains pending', () => {
  const f=fixture()
  try {
    const {ticket,assignment}=f.create();f.executor.stop(ticket,assignment,'fixture')
    assert.equal(f.executor.lifecycle('general').check(ticket,assignment,'launch'),false)
    assert.equal(f.executor.lifecycle('general').check(ticket,assignment,'deliver'),false)
  } finally { f.close() }
})
test('original Engine retry stops old provider and selects replacement under same transaction', () => {
  const f=fixture()
  try {
    const {ticket,assignment}=f.create()
    f.engine.assignmentStarted(ticket.id,assignment.id)
    f.engine.report(ticket.id,{state:'failed',summary:'synthetic failure'})
    f.requirements({...f.requirementsNow,capabilities:['specialist-role']})
    f.engine.retryTicket(ticket.id)
    assert.deepEqual(f.effects().map(row=>[row.provider,row.action]),[
      ['general','start'],['general','stop'],['specialist','start']])
  } finally { f.close() }
})
