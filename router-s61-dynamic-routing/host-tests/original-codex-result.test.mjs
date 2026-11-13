/** Production S61 Store, temporary SQL; original-native read/authority fixtures.
 * No live Codex, native ACK, Source/grant, Engine completion or independent QA.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdtempSync, rmSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { Store } from '../src/app/store.ts'
import { bindOriginalCodexResult } from '../src/executor/original_codex_result.mjs'
const sha = value => createHash('sha256').update(value).digest('hex')
function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 's61-codex-result-'))
  const store = new Store(join(directory, 'test.sqlite'))
  const binding = { taskRef: 'fixture-parent', version: '1', operationRef: 'fixture-generation',
    ownerRef: 'fixture-owner', sourceRevision: '1', sourceDigest: 'a'.repeat(64), payloadDigest: 'b'.repeat(64) }
  const mapping = { batchId: 'fixture-batch', itemId: 'fixture-item', binding }
  const ticket = { id: 'fixture-ticket', mandate: { fixture: true }, inputs: [], undelivered: [],
    assignments: [{ id: 'fixture-assignment', state: 'pending', createdAt: 'fixture-at' }] }
  store.tickets.save(ticket)
  const ackJson = JSON.stringify({ event: { type: 'thread.started', thread_id: 'fixture-child' },
    intent_id: 'fixture-intent', batch_id: mapping.batchId, item_id: mapping.itemId })
  const result = '803'
  const observation = { binding,
    item: { id: mapping.itemId, batch_id: mapping.batchId, status: 'succeeded', attempt: 1, result,
      native_context: { binding, intentId: 'fixture-intent', childCanonical: 'codex-thread:fixture-child',
        ackEvidenceSHA256: sha(ackJson), reportSHA256: sha(result) } },
    receipt: { batch_id: mapping.batchId, item_id: mapping.itemId, exit_code: 0,
      child_canonical: 'codex-thread:fixture-child', observed_execution_verified: true,
      original_result_saved: true, events_sha256: 'c'.repeat(64), result_sha256: sha(result) }, ackJson }
  let allowed = true, reads = 0
  let reader = async () => observation
  const port = bindOriginalCodexResult({ store, readOriginal: async mapping => { reads++; return reader(mapping) },
    checkCurrent: () => allowed })
  const bind = () => port.bind(ticket.id, ticket.assignments[0].id, mapping)
  const observe = () => port.observe(ticket.id, ticket.assignments[0].id)
  const record = () => JSON.parse(store.db.prepare("SELECT value FROM kv WHERE key LIKE 'router-s61/original-codex-map/%'").get().value)
  return { store, mapping, ticket, observation, port, bind, observe, record, reads: () => reads,
    readWith(fn) { reader = fn }, revoke() { allowed = false },
    close() { store.close(); rmSync(directory, { recursive: true, force: true }) } }
}

test('assignment mapping is immutable and requires the full original binding', () => {
  const f = fixture()
  try {
    assert.equal(f.bind().status, 'ORIGINAL_MAPPING_BOUND')
    assert.equal(f.bind().status, 'ORIGINAL_MAPPING_RETAINED')
    assert.throws(() => f.port.bind(f.ticket.id, f.ticket.assignments[0].id,
      { ...f.mapping, itemId: 'replacement' }), /MAPPING_CONFLICT/)
    const binding = { ...f.mapping.binding }; delete binding.payloadDigest
    assert.throws(() => f.port.bind(f.ticket.id, f.ticket.assignments[0].id,
      { ...f.mapping, binding }), /FULL_BINDING_REQUIRED/)
    assert.equal(f.reads(), 0)
  } finally { f.close() }
})

test('SQL/receipt/raw ACK evidence is captured without completing original Engine state', async () => {
  const f = fixture()
  try {
    f.bind(); const before = f.store.tickets.get(f.ticket.id)
    const first = await f.observe(); const second = await f.observe()
    assert.equal(first.status, 'ORIGINAL_CODEX_EVIDENCE_CAPTURED')
    assert.equal(first.engineCompleted, false)
    assert.equal(first.proof.result, '803'); assert.equal(first.proofDigest, second.proofDigest)
    assert.equal(f.record().phase, 'observed'); assert.equal(f.reads(), 2)
    assert.deepEqual(f.store.tickets.get(f.ticket.id), before)
  } finally { f.close() }
})

test('wrong item, digest, ACK and unverified completion cannot be captured', async () => {
  for (const mutate of [
    value => { value.item.id = 'other-item' },
    value => { value.item.attempt = 2 },
    value => { value.item.result = 'altered-result' },
    value => { value.binding.payloadDigest = 'd'.repeat(64) },
    value => { value.receipt.original_result_saved = false },
    value => { value.item.native_context.childCanonical = 'codex-thread:other' },
    value => { value.ackJson += '\n' },
  ]) {
    const f = fixture()
    try {
      f.bind(); mutate(f.observation)
      await assert.rejects(f.observe(), /MISMATCH|ACK_INVALID/)
      assert.equal(f.record().phase, 'bound')
    } finally { f.close() }
  }
})

test('pending, failed and cancelled native states remain query-only', async () => {
  const f = fixture()
  try {
    f.bind()
    for (const status of ['pending', 'failed', 'cancelled']) {
      f.observation.item.status = status
      assert.equal((await f.observe()).status, 'QUERY_ONLY_NATIVE_NOT_SUCCEEDED')
      assert.equal(f.record().phase, 'bound')
    }
  } finally { f.close() }
})

test('revocation during original read refuses evidence persistence', async () => {
  const f = fixture()
  try {
    f.bind(); f.readWith(async () => { f.revoke(); return f.observation })
    await assert.rejects(f.observe(), /CURRENT_SOURCE_DENIED/)
    assert.equal(f.record().phase, 'bound')
  } finally { f.close() }
})

test('a replacement S61 assignment cannot receive an old Codex result', async () => {
  const f = fixture()
  try {
    f.bind(); f.readWith(async () => {
      const ticket = f.store.tickets.get(f.ticket.id)
      ticket.assignments.push({ id: 'replacement-assignment', state: 'pending', createdAt: 'replacement' })
      f.store.tickets.save(ticket); return f.observation
    })
    await assert.rejects(f.observe(), /CURRENT_S61_ASSIGNMENT_REQUIRED/)
    assert.equal(f.record().phase, 'bound')
  } finally { f.close() }
})

test('a conflicting later result cannot replace captured original evidence', async () => {
  const f = fixture()
  try {
    f.bind(); await f.observe(); const original = f.record()
    f.observation.item.result = '333'
    f.observation.item.native_context.reportSHA256 = sha('333')
    f.observation.receipt.result_sha256 = sha('333')
    await assert.rejects(f.observe(), /ORIGINAL_RESULT_CONFLICT/)
    assert.deepEqual(f.record(), original)
  } finally { f.close() }
})
