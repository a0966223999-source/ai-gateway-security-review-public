/** Select an executor for ORIGINAL S61 assignments, using the ORIGINAL Store.
 * Providers are trusted adapters to original, same-Store effect queues. They
 * must never execute native/network effects synchronously inside this port.
 * This module creates no Store, queue, Source, grant, runtime or MCP server.
 */
import { createHash } from 'node:crypto'
import { providerLifecycle } from './provider_lifecycle.mjs'

const clone = value => structuredClone(value)
const fail = code => { throw new Error(code) }
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 256
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex')
const tokenList = value => Array.isArray(value) && value.length <= 16 &&
  value.every(text) && new Set(value).size === value.length
const prefix = 'router-s61/executor-selection/v1/'
const keyFor = (ticketId, assignmentId) => prefix + digest([ticketId, assignmentId])

function requirementsView(value) {
  if (!value || typeof value !== 'object' ||
      Object.keys(value).some(key => !['capabilities', 'sourceRevision', 'sourceDigest', 'requiredProvider'].includes(key)) ||
      !tokenList(value.capabilities) || !text(value.sourceRevision) ||
      !/^[a-f0-9]{64}$/.test(value.sourceDigest) ||
      (value.requiredProvider !== undefined && !text(value.requiredProvider))) {
    fail('DYNAMIC_TRUSTED_REQUIREMENTS_REQUIRED')
  }
  return { capabilities: [...value.capabilities].sort(), sourceRevision: value.sourceRevision,
    sourceDigest: value.sourceDigest,
    ...(value.requiredProvider === undefined ? {} : { requiredProvider: value.requiredProvider }) }
}

export function bindDynamicExecutor({ store, providers, policy, readRequirements, checkCurrent }) {
  if (!store || ['atomic', 'get', 'set'].some(key => typeof store[key] !== 'function') ||
      typeof store.tickets?.get !== 'function' || !Array.isArray(providers) || !providers.length ||
      providers.length > 16 || [policy, readRequirements, checkCurrent].some(fn => typeof fn !== 'function')) {
    fail('DYNAMIC_ORIGINAL_HOST_CAPABILITIES_REQUIRED')
  }
  const registry = new Map()
  for (const provider of providers) {
    if (!provider || !text(provider.id) || !text(provider.revision) || registry.has(provider.id) ||
        !tokenList(provider.capabilities) || !Number.isSafeInteger(provider.priority) ||
        Math.abs(provider.priority) > 1000 || provider.store !== store ||
        typeof provider.available !== 'function' ||
        ['start', 'deliver', 'stop'].some(key => typeof provider.executor?.[key] !== 'function')) {
      fail('DYNAMIC_ORIGINAL_QUEUE_PROVIDER_REQUIRED')
    }
    // Preserve original executor identity and copy caller-owned descriptors.
    registry.set(provider.id, Object.freeze({ id: provider.id, revision: provider.revision,
      capabilities: Object.freeze([...provider.capabilities]), priority: provider.priority,
      available: provider.available, executor: provider.executor,
      claimStart: provider.claimStart, recover: provider.recover }))
  }

  function current(ticketId, assignmentId, action) {
    const ticket = clone(store.tickets.get(ticketId))
    const assignment = action === 'stop' ? ticket?.assignments?.find(item => item.id === assignmentId)
      : ticket?.assignments?.at(-1)
    if (!ticket || ticket.id !== ticketId || !assignment || assignment.id !== assignmentId) {
      fail('DYNAMIC_CURRENT_ORIGINAL_ASSIGNMENT_REQUIRED')
    }
    if (action !== 'stop' && (ticket.ended || !['pending', 'running'].includes(assignment.state))) {
      fail('DYNAMIC_ASSIGNMENT_ENDED')
    }
    // Stopping owned work must remain possible after policy/source revocation.
    if (action === 'stop') {
      if (checkCurrent({ action, ticket: clone(ticket), assignment: clone(assignment) }) !== true) {
        fail('DYNAMIC_STOP_OWNERSHIP_DENIED')
      }
      return { ticket, assignment }
    }
    const active = policy()
    if (!active || !text(active.version) || active.paused !== false ||
        typeof active.revalidate !== 'function' || active.revalidate(clone(ticket.mandate)) !== true) {
      fail('DYNAMIC_POLICY_PAUSED_OR_REVOKED')
    }
    const requirements = requirementsView(clone(readRequirements(clone(ticket), clone(assignment))))
    if (checkCurrent({ action, ticket: clone(ticket), assignment: clone(assignment),
      requirements: clone(requirements), policyVersion: active.version }) !== true) {
      fail('DYNAMIC_CURRENT_SOURCE_DENIED')
    }
    return { ticket, assignment, requirements, policyVersion: active.version }
  }

  const identity = view => digest([view.ticket.id, view.assignment.id, view.assignment.createdAt, view.ticket.mandate])
  const context = view => digest([identity(view), view.requirements, view.policyVersion])

  function originalRecord(record, view) {
    if (!record || record.schema !== 's61-executor-route-v1' || record.phase !== 'queued' ||
        record.completionVerified !== false || record.ticketId !== view.ticket.id ||
        record.assignmentId !== view.assignment.id || record.identity !== identity(view) ||
        !text(record.providerId) || !text(record.providerRevision) || !text(record.policyVersion) ||
        !/^[a-f0-9]{64}$/.test(record.requirementsDigest) ||
        (record.stopQueued !== undefined && typeof record.stopQueued !== 'boolean')) {
      fail('DYNAMIC_ORIGINAL_ROUTE_NOT_BOUND')
    }
    return record
  }

  function choose(view) {
    const eligible = []
    for (const provider of registry.values()) {
      if (view.requirements.requiredProvider && provider.id !== view.requirements.requiredProvider) continue
      if (!view.requirements.capabilities.every(capability => provider.capabilities.includes(capability))) continue
      const available = provider.available()
      if (available !== true && available !== false) fail('DYNAMIC_PROVIDER_STATUS_UNKNOWN')
      if (available) eligible.push(provider)
    }
    eligible.sort((a, b) => b.priority - a.priority || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    if (!eligible.length) fail('DYNAMIC_NO_AUTHORIZED_AVAILABLE_ROUTE')
    return eligible[0]
  }

  function preview(ticketId, assignmentId) {
    const before = current(ticketId, assignmentId, 'preview')
    const provider = choose(before)
    const after = current(ticketId, assignmentId, 'preview')
    if (context(before) !== context(after)) fail('DYNAMIC_CONTEXT_CHANGED')
    return Object.freeze({ providerId: provider.id, providerRevision: provider.revision,
      policyVersion: after.policyVersion, requirementsDigest: digest(after.requirements),
      dispatched: false, authority: false })
  }

  function start(ticket, assignment) {
    return store.atomic(() => {
      const before = current(ticket.id, assignment.id, 'start')
      const key = keyFor(ticket.id, assignment.id)
      const prior = store.get(key)
      if (prior) {
        originalRecord(prior, before)
        return { status: 'QUERY_ONLY_ALREADY_QUEUED', providerId: prior.providerId, dispatched: false }
      }
      if (before.assignment.state !== 'pending') fail('DYNAMIC_PENDING_ASSIGNMENT_REQUIRED')
      const provider = choose(before)
      const after = current(ticket.id, assignment.id, 'start')
      if (context(before) !== context(after)) fail('DYNAMIC_CONTEXT_CHANGED')
      const record = { schema: 's61-executor-route-v1', ticketId: ticket.id, assignmentId: assignment.id,
        identity: identity(after), providerId: provider.id, providerRevision: provider.revision,
        policyVersion: after.policyVersion, requirementsDigest: digest(after.requirements),
        phase: 'queued', completionVerified: false }
      // Selection and original durable effect enqueue share the original Store
      // transaction. A rollback discards BOTH; no synchronous native send here.
      store.set(key, record)
      provider.executor.start(clone(after.ticket), clone(after.assignment))
      return { status: 'QUEUED_TO_ORIGINAL_EXECUTOR', providerId: provider.id, completionVerified: false }
    })
  }

  function retained(ticket, assignment, action, reason) {
    return store.atomic(() => {
      const before = current(ticket.id, assignment.id, action)
      const key = keyFor(ticket.id, assignment.id)
      const record = originalRecord(clone(store.get(key)), before)
      const provider = registry.get(record.providerId)
      if (!provider || provider.revision !== record.providerRevision) fail('DYNAMIC_RETAINED_PROVIDER_NOT_BOUND')
      if (action === 'deliver' && (record.policyVersion !== before.policyVersion ||
          record.requirementsDigest !== digest(before.requirements))) fail('DYNAMIC_RECORDED_SOURCE_CHANGED')
      const after = current(ticket.id, assignment.id, action)
      if (identity(before) !== identity(after) ||
          (action === 'deliver' && context(before) !== context(after))) fail('DYNAMIC_CONTEXT_CHANGED')
      if (action === 'stop') {
        if (record.stopQueued === true) return { status: 'QUERY_ONLY_STOP_ALREADY_QUEUED', dispatched: false }
        store.set(key, { ...record, stopQueued: true })
      }
      provider.executor[action](clone(after.ticket), clone(after.assignment), ...(action === 'stop' ? [reason] : []))
      return { status: 'QUEUED_TO_RETAINED_EXECUTOR', providerId: provider.id, completionVerified: false }
    })
  }

  function owned(providerId, ticketId, assignmentId, action) {
    const cleanup = action === 'stop' || action === 'shutdown' || action === 'recover'
    const view = current(ticketId, assignmentId, cleanup ? 'stop' : 'deliver')
    const record = originalRecord(clone(store.get(keyFor(ticketId, assignmentId))), view)
    const provider = registry.get(providerId)
    if (!provider || record.providerId !== provider.id || record.providerRevision !== provider.revision) {
      fail('DYNAMIC_RETAINED_PROVIDER_NOT_BOUND')
    }
    if (!cleanup && record.stopQueued === true) fail('DYNAMIC_STOP_ALREADY_QUEUED')
    if (!cleanup && (record.policyVersion !== view.policyVersion ||
        record.requirementsDigest !== digest(view.requirements))) fail('DYNAMIC_RECORDED_SOURCE_CHANGED')
    return { view, provider }
  }

  function lifecycle(providerId) {
    if (!registry.has(providerId)) fail('DYNAMIC_RETAINED_PROVIDER_NOT_BOUND')
    return Object.freeze({
      check(ticket, assignment, action) {
        try { owned(providerId, ticket.id, assignment.id, action); return true } catch { return false }
      },
      claimStart(ticket, assignment) {
        const before = owned(providerId, ticket.id, assignment.id, 'start')
        if (before.view.assignment.state !== 'pending' || typeof before.provider.claimStart !== 'function') {
          fail('DYNAMIC_ORIGINAL_NATIVE_FENCE_NOT_BOUND')
        }
        const decision = before.provider.claimStart(clone(before.view.ticket), clone(before.view.assignment))
        if (!['send', 'query-only'].includes(decision)) fail('DYNAMIC_NATIVE_FENCE_OUTCOME_UNKNOWN')
        const after = owned(providerId, ticket.id, assignment.id, 'start')
        if (context(before.view) !== context(after.view)) fail('DYNAMIC_CONTEXT_CHANGED')
        return decision
      },
      recover(ticket, assignment) {
        const { view, provider } = owned(providerId, ticket.id, assignment.id, 'recover')
        if (typeof provider.recover !== 'function') fail('DYNAMIC_ORIGINAL_QUERY_RECOVERY_NOT_BOUND')
        // Host capability must observe the original native attempt, never
        // restart/requeue it or manufacture assignmentStarted/completion.
        provider.recover(clone(view.ticket), clone(view.assignment))
      },
    })
  }

  function recover() {
    if (typeof store.tickets.active !== 'function') fail('DYNAMIC_ORIGINAL_ACTIVE_TICKETS_NOT_BOUND')
    for (const ticket of store.tickets.active()) {
      const assignment = ticket.assignments.at(-1)
      if (!assignment || !['pending', 'running'].includes(assignment.state)) continue
      const record = store.get(keyFor(ticket.id, assignment.id))
      if (!record) fail('DYNAMIC_RECOVERY_ROUTE_MISSING_QUERY_ONLY')
      lifecycle(record.providerId).recover(ticket, assignment)
    }
  }

  return Object.freeze({ preview, start, lifecycle, recover,
    deliver: (ticket, assignment) => retained(ticket, assignment, 'deliver'),
    stop: (ticket, assignment, reason) => retained(ticket, assignment, 'stop', reason) })
}

/** Optional hook in the ORIGINAL Runtime constructor. Omitted means unchanged.
 * Only a trusted loader can supply this capability; never hydrate it from
 * settings, request JSON, checkpoint data, a model result or an OAuth owner ID.
 */
export function bindRuntimeDynamicExecutor({ capability, store, policy, originalExecutor, lifetime, effects, engine }) {
  if (capability === undefined) return originalExecutor
  if (!capability || ['providers', 'readRequirements', 'checkCurrent'].some(
    key => typeof capability[key] !== 'function') || !lifetime || lifetime.aborted) {
    fail('DYNAMIC_RUNTIME_HOST_CAPABILITY_NOT_BOUND')
  }
  const registered = capability.providers({ store, originalExecutor, lifetime, effects, engine })
  if (!Array.isArray(registered) || registered.some(provider => provider.lifetime !== lifetime)) {
    fail('DYNAMIC_PROVIDER_RUNTIME_LIFETIME_MISMATCH')
  }
  if (!text(capability.originalProviderId) || !registered.some(provider =>
    provider.id === capability.originalProviderId && provider.executor === originalExecutor) ||
      registered.some(provider => typeof provider.claimStart !== 'function' || typeof provider.recover !== 'function' ||
        (provider.executor !== originalExecutor && ['attachLifecycle', 'install', 'shutdown', 'dispose'].some(
          name => typeof provider[name] !== 'function')))) {
    fail('DYNAMIC_ORIGINAL_PROVIDER_LIFECYCLE_NOT_BOUND')
  }
  const readRequirements = capability.readRequirements
  const checkCurrent = capability.checkCurrent
  const bound = bindDynamicExecutor({ store, policy, providers: registered,
    readRequirements: (ticket, assignment) => readRequirements(ticket, assignment),
    checkCurrent: request => (request.action === 'stop' || !lifetime.aborted) && checkCurrent(request) === true })
  // Refuse migration/adoption of legacy or changed active assignments before
  // any recovery callback or original Runtime loops can start.
  if (typeof store.tickets.active !== 'function') fail('DYNAMIC_ORIGINAL_ACTIVE_TICKETS_NOT_BOUND')
  for (const ticket of store.tickets.active()) {
    const assignment = ticket.assignments.at(-1)
    if (!assignment || !['pending', 'running'].includes(assignment.state)) continue
    const record = store.get(keyFor(ticket.id, assignment.id))
    if (!record || !bound.lifecycle(record.providerId).check(ticket, assignment, 'recover')) {
      fail('DYNAMIC_RECOVERY_ROUTE_MISSING_QUERY_ONLY')
    }
  }
  for (const provider of registered) {
    if (provider.executor !== originalExecutor) provider.attachLifecycle(bound.lifecycle(provider.id))
  }
  // Capture actual consumer methods before returning control to the host loader.
  const consumers = providerLifecycle(registered.filter(provider => provider.executor !== originalExecutor))
  return Object.freeze({ ...bound, ...consumers })
}
