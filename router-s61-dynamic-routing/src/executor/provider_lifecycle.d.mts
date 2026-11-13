export interface ExternalConsumerLifecycle {
  /** Register original effect consumers synchronously; do not launch native work. */
  install(): void
  /** Await cleanup owned by this provider; rejection remains visible. */
  shutdown(): Promise<void>
  dispose(): void
}
export function providerLifecycle(providers: ExternalConsumerLifecycle[]): {
  install(): void
  shutdown(): Promise<void>
  dispose(): void
}
