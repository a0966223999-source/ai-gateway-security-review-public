/** Original S61 KV mapping to an already authorized original Codex batch/item.
 * No submit/start/Stop calls, credentials, grant, queue or Engine completion.
 * Only the trusted original host supplies readOriginal/checkCurrent callbacks.
 */
import { createHash } from 'node:crypto'

const clone = value => structuredClone(value)
const fail = code => { throw new Error(code) }
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 256
const sha = value => createHash('sha256').update(value).digest('hex')
const stable = value => JSON.stringify(value, (_, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item)
const fields = ['taskRef', 'version', 'operationRef', 'ownerRef', 'sourceRevision', 'sourceDigest', 'payloadDigest']
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const keyFor = (ticketId, assignmentId) => 'router-s61/original-codex-map/v1/' + sha(stable([ticketId, assignmentId]))

function bindingOf(binding) {
  if (!binding || Object.keys(binding).length !== fields.length || Object.keys(binding).some(key => !fields.includes(key)) ||
      fields.some(key => !text(binding[key])) ||
      !hash(binding.sourceDigest) || !hash(binding.payloadDigest)) fail('CODEX_ORIGINAL_FULL_BINDING_REQUIRED')
  return Object.fromEntries(fields.map(key => [key, binding[key]]))
}
function mappingOf(value) {
  if (!value || Object.keys(value).some(key => !['batchId', 'itemId', 'binding'].includes(key)) ||
      !text(value.batchId) || !text(value.itemId)) fail('CODEX_ORIGINAL_ITEM_MAPPING_REQUIRED')
  return { batchId: value.batchId, itemId: value.itemId, binding: bindingOf(value.binding) }
}

export function bindOriginalCodexResult({ store, readOriginal, checkCurrent }) {
  if (!store || ['get', 'set', 'atomic'].some(key => typeof store[key] !== 'function') ||
      typeof store.tickets?.get !== 'function' || typeof readOriginal !== 'function' ||
      typeof checkCurrent !== 'function') fail('CODEX_ORIGINAL_RESULT_PORTS_NOT_BOUND')

  function current(ticketId, assignmentId, mapping, action) {
    const ticket = clone(store.tickets.get(ticketId))
    const assignment = ticket?.assignments?.at(-1)
    if (!ticket || ticket.id !== ticketId || ticket.ended || !assignment || assignment.id !== assignmentId ||
        !['pending', 'running'].includes(assignment.state)) fail('CODEX_CURRENT_S61_ASSIGNMENT_REQUIRED')
    if (checkCurrent({ action, ticket: clone(ticket), assignment: clone(assignment), mapping: clone(mapping) }) !== true) {
      fail('CODEX_CURRENT_SOURCE_DENIED')
    }
    return sha(stable([ticket.id, ticket.mandate, assignment.id, assignment.createdAt]))
  }

  function bind(ticketId, assignmentId, value) {
    const mapping = mappingOf(value)
    return store.atomic(() => {
      const identity = current(ticketId, assignmentId, mapping, 'bind')
      const key = keyFor(ticketId, assignmentId)
      const prior = store.get(key)
      const mappingDigest = sha(stable(mapping))
      if (prior) {
        if (prior.schema !== 's61-original-codex-map-v1' || prior.identity !== identity ||
            prior.mappingDigest !== mappingDigest || stable(prior.mapping) !== stable(mapping)) {
          fail('CODEX_ORIGINAL_MAPPING_CONFLICT')
        }
        return Object.freeze({ status: 'ORIGINAL_MAPPING_RETAINED', dispatched: false })
      }
      store.set(key, { schema: 's61-original-codex-map-v1', identity, mappingDigest, mapping, phase: 'bound' })
      return Object.freeze({ status: 'ORIGINAL_MAPPING_BOUND', dispatched: false })
    })
  }

  async function observe(ticketId, assignmentId) {
    const key = keyFor(ticketId, assignmentId)
    const prior = clone(store.get(key))
    if (!prior || prior.schema !== 's61-original-codex-map-v1' || !['bound', 'observed'].includes(prior.phase)) {
      fail('CODEX_ORIGINAL_MAPPING_NOT_BOUND')
    }
    const mapping = mappingOf(prior.mapping)
    if (sha(stable(mapping)) !== prior.mappingDigest) fail('CODEX_ORIGINAL_MAPPING_CONFLICT')
    const identity = current(ticketId, assignmentId, mapping, 'observe')
    if (identity !== prior.identity) fail('CODEX_ORIGINAL_MAPPING_CONFLICT')
    // The host reader independently reads original SQL, native receipt and raw ACK.
    // No tool/request payload can be passed here as a supposed observed result.
    const observed = clone(await readOriginal(clone(mapping)))
    current(ticketId, assignmentId, mapping, 'observe')
    if (!observed || observed.item?.status !== 'succeeded') {
      return Object.freeze({ status: 'QUERY_ONLY_NATIVE_NOT_SUCCEEDED', dispatched: false, completed: false })
    }
    const { item, receipt, ackJson } = observed
    const native = item.native_context
    if (stable(bindingOf(observed.binding)) !== stable(mapping.binding) ||
        item.id !== mapping.itemId || item.batch_id !== mapping.batchId || item.attempt !== 1 ||
        typeof item.result !== 'string' || !item.result || Buffer.byteLength(item.result) > 32768 ||
        !native || stable(bindingOf(native.binding)) !== stable(mapping.binding) ||
        !receipt || receipt.batch_id !== mapping.batchId || receipt.item_id !== mapping.itemId ||
        receipt.exit_code !== 0 || receipt.observed_execution_verified !== true || receipt.original_result_saved !== true ||
        !hash(receipt.events_sha256) || receipt.result_sha256 !== sha(item.result) ||
        native.reportSHA256 !== receipt.result_sha256 || typeof ackJson !== 'string' ||
        !ackJson || Buffer.byteLength(ackJson) > 16384 || sha(ackJson) !== native.ackEvidenceSHA256) {
      fail('CODEX_ORIGINAL_COMPLETION_EVIDENCE_MISMATCH')
    }
    let ack
    try { ack = JSON.parse(ackJson) } catch { fail('CODEX_ORIGINAL_ACK_INVALID') }
    if (ack.batch_id !== mapping.batchId || ack.item_id !== mapping.itemId ||
        ack.intent_id !== native.intentId || !text(ack.intent_id) ||
        ack.event?.type !== 'thread.started' || !text(ack.event.thread_id) ||
        receipt.child_canonical !== 'codex-thread:' + ack.event.thread_id ||
        native.childCanonical !== receipt.child_canonical) fail('CODEX_ORIGINAL_ACK_INVALID')
    const proof = { batchId: mapping.batchId, itemId: mapping.itemId,
      childCanonical: receipt.child_canonical, intentId: native.intentId,
      ackEvidenceSHA256: native.ackEvidenceSHA256, reportSHA256: receipt.result_sha256,
      eventsSHA256: receipt.events_sha256, result: item.result }
    const proofDigest = sha(stable(proof))
    return store.atomic(() => {
      if (current(ticketId, assignmentId, mapping, 'observe') !== identity) fail('CODEX_ORIGINAL_MAPPING_CONFLICT')
      const record = clone(store.get(key))
      if (record?.schema !== prior.schema || !['bound', 'observed'].includes(record.phase) ||
          record.mappingDigest !== prior.mappingDigest || record.identity !== identity ||
          stable(record.mapping) !== stable(mapping)) fail('CODEX_ORIGINAL_MAPPING_CONFLICT')
      if (record.phase === 'observed' && record.proofDigest !== proofDigest) fail('CODEX_ORIGINAL_RESULT_CONFLICT')
      if (record.phase !== 'observed') store.set(key, { ...record, phase: 'observed', proofDigest, proof })
      // Evidence capture is separate from original Engine/person/QA acceptance.
      return Object.freeze({ status: 'ORIGINAL_CODEX_EVIDENCE_CAPTURED', dispatched: false,
        engineCompleted: false, proofDigest, proof: clone(proof) })
    })
  }
  return Object.freeze({ bind, observe })
}
