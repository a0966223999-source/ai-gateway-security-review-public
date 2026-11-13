import type { Store } from '../app/store.ts'
import type { Ticket, Assignment } from '../core/index.ts'
export type OriginalCodexBinding = Record<'taskRef' | 'version' | 'operationRef' | 'ownerRef' |
  'sourceRevision' | 'sourceDigest' | 'payloadDigest', string>
export interface OriginalCodexMapping { batchId: string; itemId: string; binding: OriginalCodexBinding }
export interface OriginalCodexObservation {
  binding: OriginalCodexBinding
  item: { id: string; batch_id: string; status: string; attempt: number; result?: string;
    native_context?: { binding: OriginalCodexBinding; intentId: string; childCanonical: string;
      ackEvidenceSHA256: string; reportSHA256: string } }
  receipt?: { batch_id: string; item_id: string; child_canonical: string; exit_code: number;
    observed_execution_verified: boolean; original_result_saved: boolean;
    events_sha256: string; result_sha256: string }
  ackJson?: string
}
export function bindOriginalCodexResult(options: {
  store: Store
  readOriginal(mapping: OriginalCodexMapping): Promise<OriginalCodexObservation | undefined>
  checkCurrent(request: { action: 'bind' | 'observe'; ticket: Ticket; assignment: Assignment;
    mapping: OriginalCodexMapping }): boolean
}): Readonly<{
  bind(ticketId: string, assignmentId: string, mapping: OriginalCodexMapping): Readonly<{
    status: 'ORIGINAL_MAPPING_BOUND' | 'ORIGINAL_MAPPING_RETAINED'; dispatched: false }>
  observe(ticketId: string, assignmentId: string): Promise<Readonly<{
    status: string; dispatched: false; engineCompleted?: false; completed?: false;
    proofDigest?: string; proof?: Record<string, string> }>>
}>
