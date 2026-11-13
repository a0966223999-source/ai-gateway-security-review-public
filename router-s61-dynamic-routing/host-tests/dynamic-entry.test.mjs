import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { test } from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import * as entry from '../lib/index.js'

test('missing dynamic capability fails before registration', () => {
  for (const capability of [undefined, {}, { originalProviderId: 'dsh' }]) {
    assert.throws(() => entry.createDynamicRoutingPlugin(capability), /DYNAMIC_RUNTIME_HOST_CAPABILITY_NOT_BOUND/)
  }
})

test('trusted entry preserves the original dependency and configuration contracts', () => {
  const capability = {
    originalProviderId: 'dsh',
    providers() { throw new Error('MUST_NOT_CONSTRUCT_RUNTIME') },
    readRequirements() { throw new Error('NO_SOURCE') },
    checkCurrent() { return false },
  }
  const plugin = entry.createDynamicRoutingPlugin(capability)
  assert.equal(plugin.name, entry.name)
  assert.equal(plugin.Config, entry.Config)
  assert.equal(plugin.inject, entry.inject)
  assert.ok(Object.isFrozen(plugin))
  assert.notEqual(plugin.apply, entry.apply)
  assert.throws(() => entry.createDynamicRoutingPlugin(capability, {}), /RECOVERY_CAPABILITY_READERS_REQUIRED/)
})

test('real Cordis retains an injection-blocked entry without Runtime or dispatch', async () => {
  let providerCalls = 0
  const plugin = entry.createDynamicRoutingPlugin({
    originalProviderId: 'dsh',
    providers() { providerCalls++; throw new Error('MUST_NOT_CONSTRUCT_RUNTIME') },
    readRequirements() { throw new Error('NO_SOURCE') },
    checkCurrent() { return false },
  })
  const root = new Context()
  // Deliberately no service fixtures and no grant. Dependency injection must block startup.
  const stateDir = '/__s61_no_runtime_may_create_this_directory__'
  assert.equal(existsSync(stateDir), false)
  const fiber = root.plugin(plugin, { stateDir })
  try {
    assert.ok(fiber)
    assert.ok(root.registry.has(plugin))
    assert.equal(root.registry.has(entry), false)
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(providerCalls, 0)
    assert.equal(existsSync(stateDir), false)
  } finally {
    await fiber.dispose()
  }
})
