import { isTimeoutError } from '@/lib/with-timeout'

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error ?? '')
}

// A dial that waited past its budget (timeout, or no free spawn slot) rather
// than failing fast.
export function isStalledDialError(error: unknown): boolean {
  if (isTimeoutError(error)) {
    return true
  }

  const message = errorMessage(error)

  return message.includes('timed out while waiting for a free slot')
}

// Electron's getConnectionFor rejects with `No connection with id "…"` when
// the registry entry is gone. That is a permanent condition for the scoped
// socket, unlike transient transport errors.
export function isMissingConnectionError(error: unknown): boolean {
  const message = errorMessage(error)

  return message.includes('No connection with id')
}

// Electron's spawn guard (assertLocalProfileCanStart) rejects with these when
// the profile's directory is gone or its DELETE is still in flight. For a
// renderer socket that condition is permanent: the backend it reconnects to
// can never come back, and every retry hammers the guard (#88769).
export function isMissingProfileError(error: unknown): boolean {
  const message = errorMessage(error)

  return message.includes('no longer exists') || message.includes('is being deleted')
}

/** The handshake died before `open`. Switching to a remote connection can hit
 *  this on the first dial, then the same scope accepts a dial a second later —
 *  after the click has already aborted and the connection never loads. Auth
 *  closes (4401/4403) are a different class and are never retried. */
export function isRetryableWsHandshakeFailure(error: unknown): boolean {
  const message = errorMessage(error)

  if (/\b440[13]\b|needsOauthLogin|sign in/i.test(message)) {
    return false
  }

  return (
    message.includes('no WebSocket open within') ||
    message.includes('WebSocket error before open') ||
    message.includes('WebSocket closed during handshake') ||
    message.includes('net::ERR_CONNECTION_TIMED_OUT') ||
    message.includes('net::ERR_NETWORK_CHANGED') ||
    message.includes('net::ERR_CONNECTION_RESET') ||
    message.includes('net::ERR_CONNECTION_REFUSED')
  )
}
