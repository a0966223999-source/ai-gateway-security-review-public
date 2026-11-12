import {createHash} from 'node:crypto'
import {createNativeReportContinuation} from './native_report.mjs'
const copy = value => structuredClone(value)
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const fail = code => { throw Error(code) }
// Trusted constructor capabilities only. Public JSON can never substitute for a permit.
export function bindDurableReport({repo,getEngine,readStart,readCurrentSource,ticketId,assignmentId}) {
  if (['get','set','atomic','afterCommit'].some(k => typeof repo?.[k] !== 'function')) fail('STORE_REQUIRED')
  const start=copy(readStart()), startHash=hash(start), permits=new WeakMap()
  const engine=getEngine(), originalReport=engine.report.bind(engine)
  let capture
  const native=createNativeReportContinuation({repo,readStart,readCurrentSource,ticketId,assignmentId,
    getEngine:()=>({report:(id,report)=>{
      if(!capture || capture.calls++ || id!==ticketId || hash(report)!==hash(capture.proof.report)) fail('REPORT_CAPTURE_NOT_BOUND')
      const result=originalReport(id,report)
      capture.ticket=copy(result)
      return result
    }})})
  // One report operation per bound task/ticket; changing evidenceRef cannot escape UNKNOWN.
  const key=()=> 'native-report-receipt/v1/'+hash([start.binding,ticketId])
  function fresh() {
    const source=readCurrentSource(), ticket=repo.tickets.get(ticketId), a=ticket?.assignments.at(-1)
    if(source.approved!==true || source.revoked!==false || hash(source)!==start.sourceDigest) fail('SOURCE_CHANGED')
    if(hash(readStart())!==startHash) fail('START_CHANGED')
    if(!ticket || ticket.ended || a?.id!==assignmentId || a.state!=='running') fail('ASSIGNMENT_NOT_CURRENT')
    if(hash(ticket.mandate)!==start.mandateDigest) fail('MANDATE_CHANGED')
    return ticket
  }
  function outsideTransaction() {
    let settled=false;repo.afterCommit(()=>{settled=true})
    if(!settled) fail('COMMITTED_READ_REQUIRED')
  }
  function reserve(observation) {
    fresh()
    const proof=native.observe(observation), digest=hash(proof), ref=proof.evidenceRef
    const record={schema:'s56-commit-conditioned-report-envelope-v1',state:'unknown',startHash,ticketId,assignmentId,
      recipient:start.recipient,sourceDigest:start.sourceDigest,binding:copy(start.binding),
      evidenceRef:ref,digest,observation:copy(proof)}
    const permit=Object.freeze({evidenceRef:ref,digest}), holder={proof,record,committed:false,spent:false}
    repo.atomic(()=>{
      fresh(); const prior=repo.get(key(ref))
      if(prior!==undefined) fail(prior.digest!==digest?'REPORT_CONTENT_CONFLICT':'REPORT_QUERY_ONLY')
      repo.set(key(ref),record);repo.afterCommit(()=>{holder.committed=true})
    })
    permits.set(permit,holder);return permit
  }
  function adopt(permit) {
    const holder=permits.get(permit)
    if(!holder) fail('UNTRUSTED_REPORT_PERMIT')
    if(!holder.committed) fail('INTENT_COMMIT_REQUIRED')
    if(holder.spent) fail('REPORT_QUERY_ONLY')
    fresh();holder.spent=true
    let committed=false
    repo.atomic(()=>{
      fresh()
      if(hash(repo.get(key(permit.evidenceRef)))!==hash(holder.record)) fail('REPORT_RECORD_CHANGED')
      const observed={calls:0,proof:holder.proof}; capture=observed
      let pending
      try {pending=native.adopt(holder.proof)} finally {capture=undefined}
      if(observed.calls!==1 || pending.state!=='unknown' || pending.reason!=='OUTER_COMMIT_PENDING') fail('NATIVE_REPORT_NOT_EXECUTED')
      const receipt=native.snapshot().find(([ref])=>ref===permit.evidenceRef)?.[1]
      if(receipt?.state!=='unknown' || receipt.digest!==permit.digest) fail('NATIVE_RECEIPT_MISMATCH')
      const ticket=fresh(), report=observed.ticket?.assignments.at(-1)?.report
      if(hash(ticket)!==hash(observed.ticket) || typeof report?.at!=='string' || !report.at ||
        report.state!==holder.proof.report.state || report.summary!==holder.proof.report.summary) fail('NATIVE_RESULT_MISMATCH')
      // This is a new commit-conditioned envelope, not a copy of S28's private applied Map.
      const result={state:'applied',evidenceRef:permit.evidenceRef,ticketId,assignmentId,report:copy(report),
        durable:true,queryOnly:true,nativeTransportCryptographicallyVerified:false,sourceApprovalIsPlatformGrant:false,
        dshSessionRead:false,parentAccepted:false,parentClosed:false}
      repo.set(key(permit.evidenceRef),{...copy(holder.record),state:'applied',
        nativeInvocation:{calls:1,observationDigest:permit.digest,reportDigest:hash(report)},
        receipt:{digest:permit.digest,state:'applied'},result})
      repo.afterCommit(()=>{committed=true})
    })
    return committed ? queryOnly(permit.evidenceRef) : {state:'unknown',queryOnly:true,reason:'OUTER_COMMIT_PENDING'}
  }
  function queryOnly(ref) {
    outsideTransaction();fresh()
    if(typeof ref!=='string'||!ref.trim()) fail('EVIDENCE_REF_REQUIRED')
    const record=repo.get(key(ref))
    if(record && (record.startHash!==startHash || record.assignmentId!==assignmentId || record.evidenceRef!==ref)) fail('REPORT_BINDING_CHANGED')
    return {queryOnly:true,retryAuthorized:false,record:copy(record??{state:'not_recorded'})}
  }
  function readCommittedReport(ref) {
    const {record}=queryOnly(ref)
    if(record.state!=='applied') return undefined
    const current=fresh().assignments.at(-1).report
    if(hash(current)!==hash(record.result.report)) fail('REPORT_NOT_CURRENT')
    return copy({receipt:record.receipt,result:record.result})
  }
  return Object.freeze({reserve,adopt,queryOnly,readCommittedReport})
}
