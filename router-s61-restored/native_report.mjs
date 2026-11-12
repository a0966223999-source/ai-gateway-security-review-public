// Trusted same-holder continuation after the original native start ACK.
// Does not acquire a platform grant, close a ticket, send, or inspect DSH Session.
import {createHash} from 'node:crypto'
const clone = value => structuredClone(value)
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const fail = code => {throw new Error(code)}
const freeze = value => {if(value&&typeof value==='object'){Object.values(value).forEach(freeze);Object.freeze(value)}return value}
const states = ['progress','waiting','blocked']
export function createNativeReportContinuation({repo,getEngine,readStart,readCurrentSource,ticketId,assignmentId}) {
  if(!repo||typeof repo.afterCommit!=='function'||[getEngine,readStart,readCurrentSource].some(f=>typeof f!=='function')
    ||[ticketId,assignmentId].some(x=>typeof x!=='string'||!x))fail('CAPABILITY_REQUIRED')
  const start=clone(readStart())
  if(!start||start.state!=='observed'||start.ticketId!==ticketId||start.assignmentId!==assignmentId
    ||typeof start.recipient!=='string'||!start.binding?.operationKey)fail('START_NOT_OBSERVED')
  const startHash=hash(start), proofs=new WeakMap(), receipts=new Map()
  function fresh(){
    const source=readCurrentSource()
    if(source.approved!==true||source.revoked!==false||hash(source)!==start.sourceDigest
      ||hash(source.binding)!==hash(start.binding))fail('SOURCE_CHANGED')
    const now=readStart(),ticket=repo.tickets.get(ticketId),assignment=ticket?.assignments.at(-1)
    if(hash(now)!==startHash)fail('START_CHANGED')
    if(!ticket||ticket.ended||assignment?.id!==assignmentId||assignment.state!=='running')fail('ASSIGNMENT_NOT_CURRENT')
    if(hash(ticket.mandate)!==start.mandateDigest)fail('MANDATE_CHANGED')
    return ticket
  }
  fresh()
  function observe({sender,operationKey,evidenceRef,report}){
    if(sender!==start.recipient||operationKey!==start.binding.operationKey
      ||typeof evidenceRef!=='string'||!evidenceRef.trim())fail('OBSERVATION_NOT_BOUND')
    if(!report||Object.keys(report).sort().join('|')!=='state|summary'
      ||!states.includes(report.state)||typeof report.summary!=='string'||!report.summary.trim()
      ||report.summary.length>16384)fail('REPORT_NOT_ALLOWED')
    fresh()
    const observation=freeze({sender,operationKey,evidenceRef,report:clone(report),ticketId,assignmentId})
    const digest=hash(observation),saved=receipts.get(evidenceRef)
    if(saved&&saved.digest!==digest)fail('EVIDENCE_REF_CONFLICT')
    if(!saved)receipts.set(evidenceRef,{digest,state:'observed'})
    proofs.set(observation,{evidenceRef,digest});return observation
  }
  function adopt(proof){
    const capability=proofs.get(proof)
    if(!capability)fail('UNTRUSTED_OBSERVATION')
    fresh()
    const saved=receipts.get(capability.evidenceRef)
    if(!saved||saved.digest!==capability.digest)fail('OBSERVATION_NOT_BOUND')
    if(saved.state==='applied')return freeze({state:'already_applied',queryOnly:true,evidenceRef:proof.evidenceRef})
    if(saved.state==='unknown')fail('REPORT_OUTCOME_UNKNOWN_QUERY_ONLY')
    saved.state='unknown' // Sticky before original transaction; never infer a retry grant from a thrown call.
    const ticket=repo.atomic(()=>{
      fresh()
      const result=getEngine().report(ticketId,clone(proof.report))
      // Store callbacks settle only after the outermost SQL commit. Rollback discards them.
      repo.afterCommit(()=>{saved.state='applied'})
      return result
    })
    if(saved.state!=='applied')return freeze({state:'unknown',queryOnly:true,
      reason:'OUTER_COMMIT_PENDING',evidenceRef:proof.evidenceRef,ticketId,assignmentId,
      reportAccepted:false,parentAccepted:false,parentClosed:false})
    return freeze({state:'applied',queryOnly:false,evidenceRef:proof.evidenceRef,
      ticketId,assignmentId,report:clone(ticket.assignments.at(-1).report),
      nativeTransportCryptographicallyVerified:false,sourceApprovalIsPlatformGrant:false,
      dshSessionRead:false,durable:false,parentAccepted:false,parentClosed:false})
  }
  return Object.freeze({observe,adopt,snapshot:()=>clone([...receipts.entries()])})
}
