import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { test } from 'node:test'
import { Context } from '@deepseek-ai/cordis'
import * as entry from '../lib/index.js'

const stateDir = '/__s61_no_runtime_may_create_this_directory__'
const capability = () => ({
  originalProviderId: 'dsh',
  providers() { throw new Error('MUST_NOT_CONSTRUCT_RUNTIME') },
  readRequirements() { throw new Error('NO_SOURCE') },
  checkCurrent() { return false },
})
const registrations = root => [...root.registry.values()].filter(runtime => runtime.name === entry.name)

test('owned registration uses one native Fiber and rejects a second entry', async () => {
  const root = new Context()
  const fiber = entry.registerDynamicRoutingPlugin(root, { stateDir }, capability())
  try {
    assert.equal(registrations(root).length, 1)
    assert.throws(() => entry.registerDynamicRoutingPlugin(root, { stateDir }, capability()),
      /ORIGINAL_ENTRY_ALREADY_REGISTERED/)
    await fiber.restart()
    assert.equal(registrations(root).length, 1)
    assert.equal(existsSync(stateDir), false)
  } finally {
    await fiber.dispose()
  }
  assert.equal(registrations(root).length, 0)
})

test('existing ordinary S61 is preserved and dynamic registration is refused', async () => {
  const root = new Context()
  const ordinary = root.plugin(entry, { stateDir })
  try {
    assert.throws(() => entry.registerDynamicRoutingPlugin(root, { stateDir }, capability()),
      /ORIGINAL_ENTRY_ALREADY_REGISTERED/)
    assert.ok(root.registry.has(entry))
    assert.equal(registrations(root).length, 1)
    assert.equal(existsSync(stateDir), false)
  } finally {
    await ordinary.dispose()
  }
})

test('a directly registered dynamic entry also blocks a parallel registration', async () => {
  const root = new Context()
  const plugin = entry.createDynamicRoutingPlugin(capability())
  const original = root.plugin(plugin, { stateDir })
  try {
    assert.throws(() => entry.registerDynamicRoutingPlugin(root, { stateDir }, capability()),
      /ORIGINAL_ENTRY_ALREADY_REGISTERED/)
    assert.ok(root.registry.has(plugin))
    assert.equal(registrations(root).length, 1)
  } finally {
    await original.dispose()
  }
})

test('missing capability cannot allocate a plugin Fiber', () => {
  const root = new Context()
  const before = root.registry.size
  assert.throws(() => entry.registerDynamicRoutingPlugin(root, { stateDir }, undefined),
    /DYNAMIC_RUNTIME_HOST_CAPABILITY_NOT_BOUND/)
  assert.equal(root.registry.size, before)
})
