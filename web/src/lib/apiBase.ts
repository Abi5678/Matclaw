/** API origin for MatClaw. Prefer same-origin when UI is served by the FastAPI app. */
export function getApiBase(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    const { protocol, hostname, port } = window.location
    // Dev Vite on a non-API port → talk to the MatClaw API on 8000
    if (port && port !== '8000' && (hostname === 'localhost' || hostname === '127.0.0.1')) {
      return `${protocol}//${hostname}:8000`
    }
    return window.location.origin
  }
  return 'http://127.0.0.1:8000'
}

export function getApiWsBase(): string {
  const base = getApiBase()
  if (base.startsWith('https://')) return `wss://${base.slice('https://'.length)}`
  if (base.startsWith('http://')) return `ws://${base.slice('http://'.length)}`
  if (typeof window !== 'undefined' && window.location?.protocol === 'https:') {
    return `wss://${window.location.host}`
  }
  return 'ws://127.0.0.1:8000'
}
