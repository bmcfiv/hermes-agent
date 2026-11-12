import { ALL_PROFILES, normalizeProfileKey } from '@/store/profile'
import type { SessionInfo } from '@/types/hermes'

/**
 * Sessions visible in one sidebar profile scope.
 *
 * ALL (`__all__`) returns the caller's list unchanged — including the
 * single-profile case, where Grouping → Profile persists that sentinel
 * while grouped rendering stays off. Never filter against `__all__`.
 */
export function filterSessionsByProfileScope(sessions: SessionInfo[], profileScope: string): SessionInfo[] {
  if (profileScope === ALL_PROFILES) {
    return sessions
  }

  const scope = normalizeProfileKey(profileScope)

  return sessions.filter(session => normalizeProfileKey(session.profile) === scope)
}

/**
 * Sessions the sidebar lists for one profile ON THE WINDOW'S GATEWAY.
 *
 * A profile name is not an owner: every gateway has a `default`. A refresh merges
 * into the loaded list and keeps rows the page omitted (working, pinned, open in a
 * tile, selected: `sessionsToKeep`), and a pick between kept gateways does not wipe
 * that list, so another gateway's running chat survives the pick and, matching the
 * profile name alone, lists under this gateway too. The same chat then reads as
 * running on two machines. `isOwned` drops those rows.
 *
 * ALL (`__all__`) is the explicit cross-gateway view and stays unfiltered.
 */
export function filterSessionsByOwnerScope(
  sessions: SessionInfo[],
  profileScope: string,
  isOwned: (session: SessionInfo) => boolean
): SessionInfo[] {
  if (profileScope === ALL_PROFILES) {
    return sessions
  }

  const scope = normalizeProfileKey(profileScope)

  return sessions.filter(session => normalizeProfileKey(session.profile) === scope && isOwned(session))
}
