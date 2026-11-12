import {createHash} from 'node:crypto'
import {bindObservedStartView} from './observed_start_view.mjs'
import {bindDurableReport} from './durable_report.mjs'
import {createNativeTurnIdle} from './native_turn_idle.mjs'
const clone=x=>structuredClone(x), hash=x=>createHash('sha256').update(JSON.stringify(x)).digest('hex')
const fail=x=>{throw Error(x)}
const freeze=x=>{if(x&&typeof x==='object'){Object.values(x).forEach(freeze);Object.freeze(x)}return x}
// Constructor readers and the original Store are trusted capabilities. No JSON hydration API.
export function bindColdIdle({repo,getEngine,readCurrentSource,readCompletion,binding:inputBinding,ticketId,assignmentId,recipient,reportEvidenceRef}) {
 if([getEngine,readCurrentSource,readCompletion].some(f=>typeof f!=='function'))fail('CAPABILITY_REQUIRED')
 const binding=freeze(clone(inputBinding))
 const engine=getEngine(), turnEnded=engine.turnEnded.bind(engine)
 const view=bindObservedStartView({binding,ticketId,assignmentId,recipient,readCurrentSource,
  readOriginalStartRecord:k=>repo.get(k),readCurrentTicket:id=>repo.tickets.get(id)})
 const readStart=()=>view.read().startView, start=clone(readStart()), startHash=hash(start)
 if(hash(binding)!==hash(start.binding))fail('START_BINDING_CHANGED')
 const reports=bindDurableReport({repo,getEngine:()=>engine,readStart,readCurrentSource,ticketId,assignmentId})
 const reportKey='native-report-receipt/v1/'+hash([binding,ticketId])
 const seqKey='cold-idle-local-sequence/v1/'+hash([binding,ticketId])
 const journalKey='cold-idle-adoption/v1/'+hash([binding,ticketId,assignmentId,reportEvidenceRef])
 const permits=new WeakMap()
 function outside(){let settled=false;repo.afterCommit(()=>{settled=true});if(!settled)fail('COMMITTED_READ_REQUIRED')}
 function committed(){outside();const value=reports.readCommittedReport(reportEvidenceRef);if(!value)fail('COMPLETE_REPORT_REQUIRED');return clone(value)}
 function context(report,generation){return {binding:clone(binding),ticketId,assignmentId,recipient,sourceDigest:start.sourceDigest,
  reportEvidenceRef,reportDigest:hash(report.result.report),localGeneration:generation}}
 function completion(ctx){const c=clone(readCompletion(clone(ctx)));if(!c||c.schema!=='trusted-completion-v1'||c.completion!=='completed'||typeof c.evidenceRef!=='string'||!c.evidenceRef.trim()||hash(c.context)!==hash(ctx))fail('COMPLETION_NOT_BOUND');return c}
 // External readers first; final direct Store reads after the last Source getter.
 function finalFence(holder){
  if(hash(readStart())!==startHash)fail('START_CHANGED')
  if(hash(completion(holder.context))!==hash(holder.completion))fail('COMPLETION_CHANGED')
  const source=clone(readCurrentSource())
  if(source.approved!==true||source.revoked!==false||hash(source)!==start.sourceDigest)fail('SOURCE_CHANGED')
  const ticket=repo.tickets.get(ticketId),a=ticket?.assignments.at(-1)
  if(!ticket||ticket.ended||a?.id!==assignmentId||a.state!=='running'||hash(ticket.mandate)!==start.mandateDigest)fail('ASSIGNMENT_CHANGED')
  if(hash(repo.get(reportKey))!==holder.envelopeHash||hash(a.report)!==holder.context.reportDigest)fail('REPORT_CHANGED')
  if(ticket.undelivered.length||(a.deliveredAt&&a.deliveredAt>=a.report.at))fail('TURN_WORK_PENDING')
  if(repo.get(seqKey)!==holder.context.localGeneration)fail('LOCAL_GENERATION_CHANGED')
  if(hash(repo.get(journalKey))!==hash(holder.record))fail('JOURNAL_CHANGED')
  return ticket
 }
 function reserve(){
  if(arguments.length)fail('NO_REQUEST_INPUT')
  const report=committed(),envelopeHash=hash(repo.get(reportKey));let h
  repo.atomic(()=>{
   if(repo.get(journalKey)!==undefined)fail('IDLE_QUERY_ONLY')
   const generation=(repo.get(seqKey)??0)+1
   if(!Number.isSafeInteger(generation)||generation<1)fail('GENERATION_INVALID')
   const ctx=context(report,generation),c=completion(ctx)
   const record={schema:'cold-idle-adoption-v1',state:'unknown',context:ctx,completion:c,startHash,envelopeHash}
   h={context:ctx,completion:c,record,envelopeHash,report,committed:false,spent:false}
   repo.set(seqKey,generation);repo.set(journalKey,record);finalFence(h)
   repo.afterCommit(()=>{h.committed=true})
  })
  const p=Object.freeze({localGeneration:h.context.localGeneration});permits.set(p,h);return p
 }
 function adopt(permit){
  const h=permits.get(permit);if(!h)fail('UNTRUSTED_IDLE_PERMIT')
  if(!h.committed)fail('INTENT_COMMIT_REQUIRED');if(h.spent)fail('IDLE_QUERY_ONLY')
  outside();finalFence(h);h.spent=true
  // Committed S56 snapshot obtained before SQL; inside S29 transaction it is checked against the unchanged envelope.
  const readReport=ref=>{if(ref!==reportEvidenceRef||hash(repo.get(reportKey))!==h.envelopeHash)fail('REPORT_CHANGED');return clone(h.report)}
  let calls=0,settled=false,nativeTicket
  const native=createNativeTurnIdle({repo,readStart,readCurrentSource,readCommittedReport:readReport,ticketId,assignmentId,
   getEngine:()=>({turnEnded:(id,aid)=>{if(id!==ticketId||aid!==assignmentId||calls++)fail('IDLE_CALL_NOT_BOUND');finalFence(h);const returned=turnEnded(id,aid);nativeTicket=clone(repo.tickets.get(ticketId));return returned}})})
  const proof=native.observe({completion:'completed',sender:recipient,operationKey:start.binding.operationKey,
   evidenceRef:h.completion.evidenceRef,reportEvidenceRef,report:{state:h.report.result.report.state,summary:h.report.result.report.summary}})
  repo.atomic(()=>{
   finalFence(h);const result=native.adopt(proof)
   if(result.state!=='unknown'||result.reason!=='OUTER_COMMIT_PENDING'||calls!==1)fail('NATIVE_IDLE_NOT_EXECUTED')
   const ticket=finalFence(h),idleAt=ticket.assignments.at(-1).idleAt
   if(!nativeTicket||hash(ticket)!==hash(nativeTicket))fail('POST_IDLE_TICKET_CHANGED')
   if(typeof idleAt!=='string'||!idleAt)fail('IDLE_MISSING')
   repo.set(journalKey,{...clone(h.record),state:'applied',idleAt,nativeCalls:1})
   repo.afterCommit(()=>{settled=true})
  })
  return settled?queryOnly():{state:'unknown',queryOnly:true,reason:'OUTER_COMMIT_PENDING'}
 }
 function queryOnly(){outside();committed();const record=repo.get(journalKey);return {queryOnly:true,retryAuthorized:false,record:clone(record??{state:'not_recorded'})}}
 return Object.freeze({reserve,adopt,queryOnly})
}
