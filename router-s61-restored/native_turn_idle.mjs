// A trusted native completion observation is separate from task acceptance.
// This adapter never sends, reports ready, closes a ticket, or reads a Session.
import {createHash} from 'node:crypto'
const clone = value => structuredClone(value)
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const fail = code => { throw new Error(code) }
const freeze = value => { if(value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value) } return value }

export function createNativeTurnIdle({repo,getEngine,readStart,readCurrentSource,readCommittedReport,ticketId,assignmentId}) {
  if(!repo || typeof repo.afterCommit !== 'function'
    || [getEngine,readStart,readCurrentSource,readCommittedReport].some(f=>typeof f!=='function')
    || [ticketId,assignmentId].some(x=>typeof x!=='string'||!x)) fail('CAPABILITY_REQUIRED')
  const start=clone(readStart()), proofs=new WeakMap(), receipts=new Map()
  if(!start || start.state!=='observed' || start.ticketId!==ticketId || start.assignmentId!==assignmentId
    || typeof start.recipient!=='string' || !start.binding?.operationKey) fail('START_NOT_OBSERVED')
  const startHash=hash(start)
  function fresh() {
    const source=readCurrentSource()
    if(source.approved!==true || source.revoked!==false || hash(source)!==start.sourceDigest
      || hash(source.binding)!==hash(start.binding)) fail('SOURCE_CHANGED')
    if(hash(readStart())!==startHash) fail('START_CHANGED')
    const ticket=repo.tickets.get(ticketId), assignment=ticket?.assignments.at(-1)
    if(!ticket || ticket.ended || assignment?.id!==assignmentId || assignment.state!=='running') fail('ASSIGNMENT_NOT_CURRENT')
    if(hash(ticket.mandate)!==start.mandateDigest) fail('MANDATE_CHANGED')
    return ticket
  }
  fresh()
  function acceptedReport(observation,ticket) {
    const {sender,operationKey,reportEvidenceRef,report}=observation
    const original={sender,operationKey,evidenceRef:reportEvidenceRef,report:clone(report),ticketId,assignmentId}
    const committed=readCommittedReport(reportEvidenceRef), receipt=committed?.receipt, result=committed?.result
    if(!receipt || receipt.state!=='applied' || receipt.digest!==hash(original)) fail('REPORT_NOT_COMMITTED')
    const current=ticket.assignments.at(-1).report
    if(!result || result.state!=='applied' || result.ticketId!==ticketId || result.assignmentId!==assignmentId
      || result.evidenceRef!==reportEvidenceRef || !result.report || typeof result.report.at!=='string'
      || result.report.state!==report.state || result.report.summary!==report.summary) fail('COMMITTED_REPORT_REQUIRED')
    if(!current || hash(current)!==hash(result.report)) fail('REPORT_NOT_CURRENT')
    const assignment=ticket.assignments.at(-1)
    if(ticket.undelivered.length || (assignment.deliveredAt && assignment.deliveredAt>=result.report.at)) fail('TURN_WORK_PENDING')
  }
  function observe(observation) {
    if(!observation || Object.keys(observation).sort().join('|')!=='completion|evidenceRef|operationKey|report|reportEvidenceRef|sender'
      || observation.completion!=='completed' || observation.sender!==start.recipient
      || observation.operationKey!==start.binding.operationKey
      || [observation.evidenceRef,observation.reportEvidenceRef].some(x=>typeof x!=='string'||!x.trim())) fail('COMPLETION_NOT_BOUND')
    const report=observation.report
    if(!report || Object.keys(report).sort().join('|')!=='state|summary'
      || !['progress','waiting','blocked'].includes(report.state) || typeof report.summary!=='string'
      || !report.summary.trim() || report.summary.length>16384) fail('REPORT_NOT_ALLOWED')
    const ticket=fresh(); acceptedReport(observation,ticket)
    const proof=freeze({...clone(observation),ticketId,assignmentId}), digest=hash(proof)
    // One completion per accepted report, not per caller-selected completion ID.
    const key=proof.reportEvidenceRef, prior=receipts.get(key)
    if(prior && prior.digest!==digest) fail('COMPLETION_REF_CONFLICT')
    if(!prior) receipts.set(key,{digest,state:'observed'})
    proofs.set(proof,{key,digest,ticketHash:hash(ticket)});return proof
  }
  function adopt(proof) {
    const cap=proofs.get(proof)
    if(!cap) fail('UNTRUSTED_COMPLETION')
    const ticket=fresh();acceptedReport(proof,ticket)
    const saved=receipts.get(cap.key)
    if(!saved || saved.digest!==cap.digest) fail('COMPLETION_NOT_BOUND')
    if(saved.state==='applied') return freeze({state:'already_applied',queryOnly:true,reportEvidenceRef:cap.key})
    if(saved.state==='unknown') fail('TURN_OUTCOME_UNKNOWN_QUERY_ONLY')
    if(hash(ticket)!==cap.ticketHash) fail('TURN_CONTEXT_CHANGED')
    saved.state='unknown'
    const after=repo.atomic(()=>{
      const before=fresh();acceptedReport(proof,before)
      if(hash(before)!==cap.ticketHash) fail('TURN_CONTEXT_CHANGED')
      getEngine().turnEnded(ticketId,assignmentId)
      const result=repo.tickets.get(ticketId), expected=clone(before), idleAt=result?.assignments.at(-1)?.idleAt
      if(typeof idleAt!=='string'||!idleAt) fail('IDLE_NOT_RECORDED')
      expected.assignments.at(-1).idleAt=idleAt
      if(hash(result)!==hash(expected)) fail('UNEXPECTED_TURN_EFFECT')
      repo.afterCommit(()=>{saved.state='applied'})
      return result
    })
    if(saved.state!=='applied') return freeze({state:'unknown',queryOnly:true,reason:'OUTER_COMMIT_PENDING',turnIdleRecorded:false})
    return freeze({state:'applied',queryOnly:false,ticketId,assignmentId,idleAt:after.assignments.at(-1).idleAt,
      turnIdleRecorded:true,taskAccepted:false,parentClosed:false,formalSession:false,durableProof:false})
  }
  return Object.freeze({observe,adopt,snapshot:()=>clone([...receipts.entries()])})
}
