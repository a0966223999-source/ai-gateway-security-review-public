import type { Executor, Policy, Ticket, Assignment } from '../core/index.ts'
import type { ExecutorRouting } from './dsh.ts'
import type { Store } from '../app/store.ts'

export interface RoutingRequirements {
  capabilities: string[]
  sourceRevision: string
  sourceDigest: string
  requiredProvider?: string
}
export interface QueueProvider {
  id: string
  revision: string
  capabilities: string[]
  priority: number
  store: Store
  available(): boolean
  executor: Executor
  /** Original durable native once fence; UNKNOWN is query-only. */
  claimStart?(ticket: Ticket, assignment: Assignment): 'send' | 'query-only'
  /** Query original attempt only; no automatic resend or new assignment. */
  recover?(ticket: Ticket, assignment: Assignment): void
  /** Required for every external executor before Runtime starts. */
  attachLifecycle?(routing: ExecutorRouting): void
}
export interface RoutingCheck {
  action: 'start' | 'deliver' | 'stop' | 'preview'
  ticket: Ticket
  assignment: Assignment
  requirements?: RoutingRequirements
  policyVersion?: string
}
export interface DynamicRoutingCapability {
  originalProviderId: string
  providers(host: { store: Store; originalExecutor: Executor; lifetime: AbortSignal }):
    (QueueProvider & { lifetime: AbortSignal })[]
  readRequirements(ticket: Ticket, assignment: Assignment): RoutingRequirements
  checkCurrent(request: RoutingCheck): boolean
}
export interface DynamicExecutor extends Executor {
  lifecycle(providerId: string): ExecutorRouting
  recover(): void
  preview(ticketId: string, assignmentId: string): Readonly<{
    providerId: string; providerRevision: string; policyVersion: string
    requirementsDigest: string; dispatched: false; authority: false
  }>
}
export function bindDynamicExecutor(options: {
  store: Store
  providers: QueueProvider[]
  policy(): Policy
  readRequirements(ticket: Ticket, assignment: Assignment): RoutingRequirements
  checkCurrent(request: RoutingCheck): boolean
}): DynamicExecutor
export function bindRuntimeDynamicExecutor(options: {
  capability?: DynamicRoutingCapability
  store: Store
  policy(): Policy
  originalExecutor: Executor
  lifetime: AbortSignal
}): Executor
