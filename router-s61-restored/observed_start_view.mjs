import {createHash} from 'node:crypto'
const clone = value => structuredClone(value)
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const fail = code => { throw Error(code) }
const nonempty = value => typeof value === 'string' && value.trim().length > 0
const exact = (value, keys) => value && typeof value === 'object' &&
  Object.keys(value).sort().join('|') === [...keys].sort().join('|')
const freeze = value => {
  if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) }
  return value
}
const bindingKeys = ['parentRef','parentVersion','taskRef','version','operationKey','owner']
const intentKeys = ['key','ticketId','assignmentId','mandateDigest','binding','recipient','sourceDigest','title','state']
// Trusted constructor only: these readers are host capabilities, not request JSON.
// There is deliberately no hydration, grant, sender, report, or idle mutation API.
export function bindObservedStartView({binding,ticketId,assignmentId,recipient,
  readCurrentSource,readOriginalStartRecord,readCurrentTicket}) {
  if (!exact(binding,bindingKeys) || !bindingKeys.every(k=>nonempty(binding[k])) ||
    ![ticketId,assignmentId,recipient].every(nonempty) || !recipient.startsWith('/root/') ||
    [readCurrentSource,readOriginalStartRecord,readCurrentTicket].some(f=>typeof f !== 'function')) {
    fail('START_VIEW_CAPABILITY_REQUIRED')
  }
  const expected = freeze(clone(binding))
  const initialSource = clone(readCurrentSource())
  const sourceHash = hash(initialSource)
  const recordKey = 'native-start-attempt/v1/' + hash([expected,ticketId])
  function validateSource(source) {
    if (!source || source.approved !== true || source.revoked !== false ||
      hash(source.binding) !== hash(expected) || hash(source) !== sourceHash) fail('START_VIEW_SOURCE_CHANGED')
  }
  validateSource(initialSource)
  function sample() {
    // Clone each result immediately, before another getter can mutate an alias.
    const source = clone(readCurrentSource())
    const record = clone(readOriginalStartRecord(recordKey))
    const ticket = clone(readCurrentTicket(ticketId))
    validateSource(source)
    if (!record || record.schema !== 'native-start-attempt-v1' || record.state !== 'observed') fail('START_VIEW_NOT_OBSERVED')
    const intent = record.intent, receipt = record.receipt
    if (!exact(intent,intentKeys) || intent.state !== 'unknown' || intent.key !== `${ticketId}/${assignmentId}` ||
      intent.ticketId !== ticketId || intent.assignmentId !== assignmentId || intent.recipient !== recipient ||
      hash(intent.binding) !== hash(expected) || intent.sourceDigest !== sourceHash ||
      typeof intent.title !== 'string' || !/^[a-f0-9]{64}$/.test(intent.mandateDigest)) fail('START_VIEW_INTENT_INVALID')
    const intentDigest = hash(intent)
    if (record.ticketId !== ticketId || record.assignmentId !== assignmentId || record.recipient !== recipient ||
      hash(record.binding) !== hash(expected) || record.sourceHash !== sourceHash || record.intentDigest !== intentDigest) fail('START_VIEW_RECORD_MISMATCH')
    if (!receipt || receipt.schema !== 'native-start-result-receipt-v1' || receipt.state !== 'observed' ||
      hash(receipt.binding) !== hash(expected) || receipt.sourceHash !== sourceHash ||
      receipt.ticketId !== ticketId || receipt.assignmentId !== assignmentId || receipt.intentDigest !== intentDigest ||
      receipt.sender !== recipient || !nonempty(receipt.evidenceRef) ||
      !['progress','waiting','blocked'].includes(receipt.reportedState) || typeof receipt.summary !== 'string' ||
      ['nativeSendExecuted','formalPlatformGrant','parentAccepted','retryAuthorized'].some(k=>receipt[k] !== false)) fail('START_VIEW_RECEIPT_INVALID')
    const assignment = ticket?.assignments?.at(-1)
    if (!ticket || ticket.id !== ticketId || ticket.ended || assignment?.id !== assignmentId ||
      assignment.state !== 'running') fail('START_VIEW_ASSIGNMENT_NOT_CURRENT')
    if (hash(ticket.mandate) !== intent.mandateDigest) fail('START_VIEW_MANDATE_CHANGED')
    return {source,record,ticket}
  }
  function read() {
    if (arguments.length) fail('START_VIEW_NO_REQUEST_INPUT')
    const before = sample(), after = sample()
    if (hash(before) !== hash(after)) fail('START_VIEW_UNSTABLE_READ')
    // Last external getter: recheck revocation after all record/ticket reads.
    validateSource(clone(readCurrentSource()))
    // Exact S23 row projection: change state only, no metadata inside startView.
    const startView = clone(before.record.intent)
    startView.state = 'observed'
    return freeze({startView,meta:{schema:'observed-start-view-query-v1',queryOnly:true,
      sendAllowed:false,retryAuthorized:false,nativeLiveStatusVerified:false,
      formalPlatformGrant:false,completionVerified:false,recordKey,
      sourceDigest:sourceHash,startViewDigest:hash(startView)}})
  }
  return Object.freeze({read})
}
