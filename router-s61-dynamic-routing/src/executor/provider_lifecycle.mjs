/** Own external consumers within the ORIGINAL Runtime. No queue or grant is created. */
export function providerLifecycle(providers) {
  const entries = providers.map(provider => ({
    install: provider.install.bind(provider),
    shutdown: provider.shutdown.bind(provider),
    dispose: provider.dispose.bind(provider),
  }))
  let installed = false
  let disposed = false
  let stopping
  const attempted = []
  function dispose() {
    if (disposed) return
    disposed = true
    const failures = []
    for (const entry of [...attempted].reverse()) {
      try { entry.dispose() } catch (error) { failures.push(error) }
    }
    if (failures.length) throw new AggregateError(failures, 'DYNAMIC_PROVIDER_DISPOSE_FAILED')
  }
  return Object.freeze({
    install() {
      if (disposed || stopping) throw new Error('DYNAMIC_PROVIDER_LIFETIME_ENDED')
      if (installed) return
      try {
        for (const entry of entries) {
          attempted.push(entry)
          // Registration only. Native work belongs to the original effect consumer.
          const result = entry.install()
          if (result !== undefined) throw new Error('DYNAMIC_PROVIDER_INSTALL_MUST_BE_SYNCHRONOUS_VOID')
        }
        installed = true
      } catch (error) {
        try { dispose() } catch (cleanup) {
          throw new AggregateError([error, cleanup], 'DYNAMIC_PROVIDER_INSTALL_AND_CLEANUP_FAILED')
        }
        throw error
      }
    },
    shutdown() {
      // Invoke every owner even when another throws synchronously; await all cleanup.
      return stopping ??= Promise.allSettled(attempted.map(entry =>
        Promise.resolve().then(() => entry.shutdown()))).then(results => {
        const failures = results.filter(result => result.status === 'rejected').map(result => result.reason)
        if (failures.length) throw new AggregateError(failures, 'DYNAMIC_PROVIDER_SHUTDOWN_FAILED')
      })
    },
    dispose,
  })
}
