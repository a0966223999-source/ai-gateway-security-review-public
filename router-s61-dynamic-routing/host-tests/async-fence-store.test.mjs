/** Original Store/Effects/Engine with synthetic identity and native fence callbacks.
 * Temporary SQL only; no live grant, Codex, Work, model or formal Runtime.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Store } from '../src/app/store.ts'
import { Effects } from '../src/app/effects.ts'
import { HealthBoard } from '../src/infra/health.ts'
import { Engine } from '../src/core/engine.ts'
import { bindDynamicExecutor } from '../src/executor/dynamic_executor.mjs'

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 's61-production-store-'))
  const store = new Store(join(directory, 'test.sqlite'))
  const effects = new Effects(store, new HealthBoard())
  let allowed = true, launches = 0, failQueue = false, claims = 0, queries = 0
  let nativeClaim = async () => 'send'
  const policy = { version: 'fixture-policy', paused: false, revalidate: () => allowed,
    gate: () => ({ admissible: true, strength: 'explicit', basis: { via: 'listed' } }),
    admitsInferred: () => false, evalTimeoutMs: 60000 }
  const provider = {
    id: 'fixture-codex', revision: 'fixture-v1', store, capabilities: ['read'], priority: 1,
    available: () => true,
    claimStartAsync: async (...args) => { claims++; return nativeClaim(...args) },
    recover: () => { queries++ },
    executor: {
      start(ticket, assignment) {
        effects.enqueue('launch', assignment.id, { ticketId: ticket.id, assignmentId: assignment.id })
        if (failQueue) throw Error('QUEUE_ROLLBACK_PROBE')
      },
      deliver() { throw Error('UNEXPECTED_DELIVERY') },
      stop() { throw Error('UNEXPECTED_STOP') },
    },
  }
  const executor = bindDynamicExecutor({ store, providers: [provider], policy: () => policy,
    readRequirements: () => ({ capabilities: ['read'], sourceRevision: 'fixture-source', sourceDigest: 'a'.repeat(64) }),
    checkCurrent: () => allowed })
  effects.handle('launch', async ({ ticketId, assignmentId }) => {
    const ticket = store.tickets.get(ticketId)
    const assignment = ticket.assignments.find(a => a.id === assignmentId)
    const decision = await executor.lifecycle(provider.id).claimStartAsync(ticket, assignment)
    if (decision === 'send') launches++
    // This probe never reports a native ACK/completion to the Engine.
  })
  const engine = new Engine({ repo: store, executor, policy: () => policy,
    scope: { scope: () => ({ listened: true, observed: false }) },
    assessor: { request() {} }, outbox: { send() {}, status() {} },
    now: () => '2026-11-13T00:00:00.000Z' })
  function create() {
    engine.receive({ id: 'fixture-trigger', source: 'fixture', threads: [{ source: 'fixture', id: 'thread' }],
      actor: { source: 'fixture', id: 'owner' }, human: true, addressed: true,
      at: '2026-11-13T00:00:00.000Z', text: 'Synthetic fixture, no dispatch authority', meta: {} })
    return engine.decide('fixture-trigger', { kind: 'accept', title: 'fixture', scope: 'fixture', reason: 'fixture' })
  }
  return { store, effects, provider, create, executor,
    revoke() { allowed = false }, failQueue() { failQueue = true },
    claim(fn) { nativeClaim = fn },
    counts: () => ({ launches, claims, queries }),
    rows: () => store.db.prepare('SELECT * FROM effect').all(),
    async close() { await effects.drain(); store.close(); rmSync(directory, { recursive: true, force: true }) },
  }
}

test('real Store and Effects wait for native SQL fence outside the route transaction', async () => {
  const f = fixture()
  let finish
  f.claim(() => new Promise(resolve => { finish = resolve }))
  try {
    f.create()
    assert.deepEqual(f.counts(), { launches: 0, claims: 0, queries: 0 })
    assert.equal(f.rows().length, 1)
    f.effects.start()
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(f.counts(), { launches: 0, claims: 1, queries: 0 })
    // The native await holds no original Store transaction open.
    assert.equal(f.store.db.isTransaction, false)
    finish('send')
    await f.effects.idle()
    assert.deepEqual(f.counts(), { launches: 1, claims: 1, queries: 0 })
    assert.equal(f.rows().length, 0)
  } finally { if (finish) finish('query-only'); await f.close() }
})

test('revocation during an async native fence refuses launch and retains original effect', async () => {
  const f = fixture()
  let finish
  f.claim(() => new Promise(resolve => { finish = resolve }))
  try {
    f.create(); f.effects.start()
    await new Promise(resolve => setImmediate(resolve))
    f.revoke(); finish('send')
    await f.effects.idle()
    assert.deepEqual(f.counts(), { launches: 0, claims: 1, queries: 0 })
    assert.equal(f.rows().length, 1)
    assert.equal(f.rows()[0].attempts, 1)
    assert.match(f.rows()[0].error, /REVOKED|DENIED/)
  } finally { if (finish) finish('query-only'); await f.close() }
})

test('unknown async native reservation never becomes a send decision', async () => {
  const f = fixture()
  f.claim(async () => { throw Error('ORIGINAL_RESERVATION_UNKNOWN_QUERY_ONLY') })
  try {
    f.create(); f.effects.start(); await f.effects.idle()
    assert.deepEqual(f.counts(), { launches: 0, claims: 1, queries: 0 })
    assert.equal(f.rows().length, 1)
    assert.match(f.rows()[0].error, /RESERVATION_UNKNOWN/)
    const ticket = f.store.tickets.active()[0]
    assert.throws(() => f.executor.lifecycle(f.provider.id).claimStart(ticket, ticket.assignments.at(-1)),
      /ASYNC_FENCE_ALREADY_ATTEMPTED_QUERY_ONLY/)
    f.store.db.prepare('UPDATE effect SET due = 0').run()
    await f.effects.idle()
    assert.deepEqual(f.counts(), { launches: 0, claims: 1, queries: 1 })
    assert.equal(f.store.tickets.active().length, 1)
  } finally { await f.close() }
})

test('production Store rolls back ticket, route and production Effects enqueue together', async () => {
  const f = fixture()
  try {
    f.failQueue()
    assert.throws(() => f.create(), /QUEUE_ROLLBACK_PROBE/)
    assert.equal(f.store.tickets.list().length, 0)
    assert.equal(f.rows().length, 0)
    assert.equal(f.store.db.prepare("SELECT COUNT(*) AS n FROM kv WHERE key LIKE 'router-s61/%'").get().n, 0)
    assert.deepEqual(f.counts(), { launches: 0, claims: 0, queries: 0 })
  } finally { await f.close() }
})
