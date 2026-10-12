/**
 * persisted-flag.ts
 *
 * A boolean preference the main process owns and keeps in a tiny
 * `{ "on": true }` JSON file under userData, so a cold launch restores it
 * before any window exists. The renderer flips it over IPC. Anything but an
 * explicit `on: true` (missing, unreadable, malformed) reads as off.
 */

import fs from 'node:fs'
import path from 'node:path'

export interface PersistedFlag {
  read: () => boolean
  write: (on: boolean) => void
}

/** Backed by `<dir>/<name>.json`; `name` also labels write-failure logs. */
export function createPersistedFlag(dir: string, name: string, log: (line: string) => void): PersistedFlag {
  const filePath = path.join(dir, `${name}.json`)

  return {
    read: () => {
      try {
        return JSON.parse(fs.readFileSync(filePath, 'utf8')).on === true
      } catch {
        return false
      }
    },
    write: on => {
      try {
        fs.mkdirSync(path.dirname(filePath), { recursive: true })
        fs.writeFileSync(filePath, JSON.stringify({ on }, null, 2), 'utf8')
      } catch (error) {
        log(`[${name}] write failed: ${error instanceof Error ? error.message : String(error)}`)
      }
    }
  }
}
