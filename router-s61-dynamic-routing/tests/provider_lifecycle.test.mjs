/** Synthetic consumers: verifies wiring, not native execution or stop proof. */
import test from 'node:test'
import assert from 'node:assert/strict'
import { providerLifecycle } from '../src/executor/provider_lifecycle.mjs'
function fixture() {
  const log = []
  const providers = ['a', 'b'].map(id => ({
    install() { log.push('install:' + id) },
    async shutdown() { log.push('shutdown:' + id) },
    dispose() { log.push('dispose:' + id) },
  }))
  return { log, providers }
}
test('registers consumers once and disposes in reverse order without launching', async () => {
  const f = fixture(), bound = providerLifecycle(f.providers)
  bound.install(); bound.install()
  assert.deepEqual(f.log, ['install:a', 'install:b'])
  const first = bound.shutdown()
  assert.equal(bound.shutdown(), first)
  await first
  bound.dispose(); bound.dispose()
  assert.deepEqual(f.log, ['install:a', 'install:b', 'shutdown:a', 'shutdown:b', 'dispose:b', 'dispose:a'])
  assert.throws(() => bound.install(), /LIFETIME_ENDED/)
})
test('waits for every native owner before shutdown resolves', async () => {
  const f = fixture()
  let finish
  f.providers[0].shutdown = () => new Promise(resolve => { finish = resolve })
  const bound = providerLifecycle(f.providers); bound.install()
  let done = false
  const stopping = bound.shutdown().then(() => { done = true })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(done, false)
  assert(f.log.includes('shutdown:b'))
  finish(); await stopping; assert.equal(done, true)
})
test('one synchronous shutdown failure cannot skip another provider', async () => {
  const f = fixture()
  f.providers[0].shutdown = () => { throw new Error('native-stop-unknown') }
  const bound = providerLifecycle(f.providers); bound.install()
  await assert.rejects(bound.shutdown(), error => {
    assert.equal(error.message, 'DYNAMIC_PROVIDER_SHUTDOWN_FAILED')
    assert.equal(error.errors[0].message, 'native-stop-unknown')
    return true
  })
  assert(f.log.includes('shutdown:b'))
  bound.dispose()
  assert(f.log.includes('dispose:a'))
})
test('failed partial installation cleans even the failing consumer, retaining cause', async () => {
  const f = fixture()
  f.providers[1].install = () => { f.log.push('partial:b'); throw Error('registration-failed') }
  const bound = providerLifecycle(f.providers)
  assert.throws(() => bound.install(), /registration-failed/)
  assert.deepEqual(f.log, ['install:a', 'partial:b', 'dispose:b', 'dispose:a'])
  await bound.shutdown()
  assert(f.log.includes('shutdown:b'))
})
test('captures owner methods instead of following a mutated registration object', async () => {
  const f = fixture(), bound = providerLifecycle(f.providers)
  f.providers[0].install = () => { throw Error('replaced') }
  f.providers[0].shutdown = () => { throw Error('replaced') }
  bound.install(); await bound.shutdown()
  assert(f.log.includes('install:a')); assert(f.log.includes('shutdown:a'))
})
test('cleanup failures do not prevent other consumers disposing', () => {
  const f = fixture()
  f.providers[1].dispose = () => { throw Error('dispose-failed') }
  const bound = providerLifecycle(f.providers); bound.install()
  assert.throws(() => bound.dispose(), /PROVIDER_DISPOSE_FAILED/)
  assert(f.log.includes('dispose:a'))
})
test('installation and cleanup failures are both retained', () => {
  const f = fixture()
  f.providers[1].install = () => { throw Error('install-failed') }
  f.providers[1].dispose = () => { throw Error('dispose-failed') }
  const bound = providerLifecycle(f.providers)
  assert.throws(() => bound.install(), error => {
    assert.equal(error.message, 'DYNAMIC_PROVIDER_INSTALL_AND_CLEANUP_FAILED')
    assert.equal(error.errors[0].message, 'install-failed')
    return true
  })
})
