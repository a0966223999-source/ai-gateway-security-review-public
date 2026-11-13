/** Main plugin entry: mounts the Runtime and provides its restart callback for deployment account changes. */
import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-settings'
import type { DynamicRoutingCapability } from './executor/dynamic_executor.mjs'
import { Runtime } from './app/runtime.ts'
import type { RecoveryColdIdleCapability } from './app/recovery/source_recovery_controller.mjs'
import { Config, machineOf, type DeploymentConfig } from './deployment.ts'

export const name = 'dsh-superagent'
export const inject = ['agents', 'sessionController', 'sessionPersistence', 'workspaceRegistry', 'permissionPresets', 'tools', 'systemPrompt', 'skills', 'webServer', 'llm', 'subprocess']
export { Config }
export async function apply(ctx: Context, config: DeploymentConfig): Promise<void> {
  return applyRuntime(ctx, config)
}
/** Explicit trusted-loader entry. A capability is not configuration or Source JSON. */
export async function applyWithRecovery(ctx: Context, config: DeploymentConfig, coldIdle: RecoveryColdIdleCapability): Promise<void> {
  // Shape check only, not a grant: reject before machine construction or any Runtime side effects.
  if (!coldIdle || typeof coldIdle !== 'object' || typeof coldIdle.readCurrentSource !== 'function' || typeof coldIdle.readCompletion !== 'function') {
    throw new TypeError('RECOVERY_CAPABILITY_READERS_REQUIRED')
  }
  return applyRuntime(ctx, config, coldIdle)
}
/** Same original Runtime; only an owned host loader can provide these live capabilities. */
export async function applyWithDynamicRouting(ctx: Context, config: DeploymentConfig, dynamic: DynamicRoutingCapability,
  coldIdle?: RecoveryColdIdleCapability): Promise<void> {
  validateDynamicRoutingCapability(dynamic, coldIdle)
  return applyRuntime(ctx, config, coldIdle, dynamic)
}
/**
 * The owned loader registers this object IN PLACE OF this module's ordinary plugin.
 * Keep the returned object stable across restarts; never put capabilities in config.
 * Constructing the entry neither creates a Runtime nor authorizes a dispatch.
 */
export function createDynamicRoutingPlugin(dynamic: DynamicRoutingCapability,
  coldIdle?: RecoveryColdIdleCapability) {
  validateDynamicRoutingCapability(dynamic, coldIdle)
  return Object.freeze({
    name,
    inject,
    Config,
    apply(ctx: Context, config: DeploymentConfig): Promise<void> {
      return applyWithDynamicRouting(ctx, config, dynamic, coldIdle)
    },
  })
}
/** Register at the owned loader's original entry point, never beside an existing S61. */
export function registerDynamicRoutingPlugin(ctx: Context, config: DeploymentConfig,
  dynamic: DynamicRoutingCapability, coldIdle?: RecoveryColdIdleCapability) {
  const plugin = createDynamicRoutingPlugin(dynamic, coldIdle)
  if (ctx.registry.has({ apply }) || [...ctx.registry.values()].some(runtime => runtime.name === name)) {
    throw new Error('DYNAMIC_RUNTIME_ORIGINAL_ENTRY_ALREADY_REGISTERED')
  }
  // Return the native Fiber/thenable: startup failures, restart and disposal retain Cordis ownership.
  return ctx.plugin(plugin, config)
}
function validateDynamicRoutingCapability(dynamic: DynamicRoutingCapability,
  coldIdle?: RecoveryColdIdleCapability): void {
  if (!dynamic || !dynamic.originalProviderId || ['providers', 'readRequirements', 'checkCurrent'].some(
    key => typeof dynamic[key as keyof DynamicRoutingCapability] !== 'function')) {
    throw new TypeError('DYNAMIC_RUNTIME_HOST_CAPABILITY_NOT_BOUND')
  }
  if (coldIdle && (typeof coldIdle.readCurrentSource !== 'function' || typeof coldIdle.readCompletion !== 'function')) {
    throw new TypeError('RECOVERY_CAPABILITY_READERS_REQUIRED')
  }
}
async function applyRuntime(ctx: Context, config: DeploymentConfig, coldIdle?: RecoveryColdIdleCapability,
  dynamic?: DynamicRoutingCapability): Promise<void> {
  const machine = machineOf(config)
  // The management panel provides its own settings UI, so disable DSH's auto-generated settings page.
  ctx.inject(['settings'], child => { child.effect(() => child.settings.configure({ auto: false }, ctx.fiber)) })
  // Volatile limits update in place without a restart; account changes restart dsh-superagent. DSH's settings service stops
  // serving this entry while dsh-superagent restarts and rereads it only on a configuration write, so trigger `describe()`
  // after restart to keep open configuration forms editable without a page reload.
  // TODO: drop the read once DSH's settings service rereads an entry whose plugin becomes active again.
  const restart = (): void => {
    ctx.fiber.restart().then(() => { ctx.get('settings')?.describe() }).catch((error: unknown) => { ctx.logger.error('dsh-superagent restart failed: %s', String(error)) })
  }
  const runtime = new Runtime(ctx, machine, restart, coldIdle, dynamic)
  ctx.effect(() => async () => { await runtime.stop() })
  await runtime.ready
  ctx.logger.info(runtime.configured() ? 'dsh-superagent ready; the Superagent sidebar entry opens the management panel' : `dsh-superagent is waiting for setup: ${runtime.setupProblem?.detail ?? ''}`)
}
export type { Deployment, DeploymentConfig } from './deployment.ts'
export * from './core/index.ts'
