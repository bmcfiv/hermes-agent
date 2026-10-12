/**
 * chrome-open.ts
 *
 * Opt-in route for clicked web links: open them in Google Chrome on macOS, in
 * the Chrome profile that is signed in to the account the link names.
 *
 * Why not shell.openExternal: that hands the URL to the OS default browser,
 * which may not be Chrome, and even when it is, Chrome opens it in whichever
 * profile window is frontmost. A Google Docs/Drive link for a work account
 * then lands in a personal profile and shows "You need access".
 *
 * Account resolution: a Google URL carrying `authuser=<email>` opens in the
 * profile whose signed-in account is that email, read from Chrome's
 * `Local State` (`profile.info_cache[].user_name`). Display names are never
 * used — they are user-set labels unrelated to the signed-in identity. No
 * match, or no `authuser`, opens in Chrome's last-used profile.
 *
 * Off by default: main.ts only calls this when the "Open links in Google
 * Chrome" setting is on, and every miss falls back to the OS default browser.
 *
 * Electron-free: every side effect is injected (systemChromeOpenDeps wires the
 * real ones) so this unit-tests without electron (same shape as external-open.ts).
 */

import { type ChildProcess, spawn, type SpawnOptions } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'

export const MAC_CHROME_BINARY = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

const SKIP_PROFILES = new Set(['Guest Profile', 'System Profile'])

export interface ChromeOpenDeps {
  platform: NodeJS.Platform
  homedir: string
  exists: (path: string) => boolean
  readFile: (path: string) => string
  spawn: (cmd: string, args: readonly string[], opts: SpawnOptions) => ChildProcess
  log: (line: string) => void
}

/** The real filesystem, platform and process spawner. */
export function systemChromeOpenDeps(log: (line: string) => void): ChromeOpenDeps {
  return {
    platform: process.platform,
    homedir: os.homedir(),
    exists: candidate => fs.existsSync(candidate),
    readFile: file => fs.readFileSync(file, 'utf8'),
    spawn: (cmd, args, opts) => spawn(cmd, args, opts),
    log
  }
}

/** The email a URL asks to be opened as, or null. Only `authuser=<email>`
 *  counts — a numeric `authuser=1` is an index into one profile's own
 *  account list and says nothing about which Chrome profile to use. */
export function accountEmailForUrl(rawUrl: string): null | string {
  try {
    const value = new URL(rawUrl).searchParams.get('authuser')?.trim().toLowerCase()

    return value && value.includes('@') ? value : null
  } catch {
    return null
  }
}

/** Chrome profile directory signed in as `email`, from `Local State` JSON.
 *  Null when zero or several profiles match — better Chrome's last-used
 *  profile than a guess. */
export function chromeProfileForEmail(localStateJson: string, email: string): null | string {
  let cache: Record<string, { user_name?: string }>

  try {
    cache = JSON.parse(localStateJson)?.profile?.info_cache ?? {}
  } catch {
    return null
  }

  const target = email.toLowerCase()

  const hits = Object.entries(cache)
    .filter(([dir, info]) => !SKIP_PROFILES.has(dir) && String(info?.user_name ?? '').toLowerCase() === target)
    .map(([dir]) => dir)

  return hits.length === 1 ? hits[0] : null
}

/**
 * Open an http(s) URL in Chrome. Resolves true when Chrome was launched,
 * false when this route does not apply (not macOS, Chrome not installed,
 * not a web URL, spawn threw) so the caller falls back to the OS default.
 */
export async function openInChrome(rawUrl: string, deps: ChromeOpenDeps): Promise<boolean> {
  if (deps.platform !== 'darwin' || !/^https?:\/\//i.test(rawUrl) || !deps.exists(MAC_CHROME_BINARY)) {
    return false
  }

  const args: string[] = []
  const email = accountEmailForUrl(rawUrl)

  if (email) {
    let profile: null | string = null

    try {
      const localState = deps.readFile(`${deps.homedir}/Library/Application Support/Google/Chrome/Local State`)
      profile = chromeProfileForEmail(localState, email)
    } catch (error) {
      deps.log(`[link] chrome Local State unreadable: ${error instanceof Error ? error.message : String(error)}`)
    }

    if (profile) {
      args.push(`--profile-directory=${profile}`)
    } else {
      deps.log(`[link] no single chrome profile signed in as ${email}; using last-used profile`)
    }
  }

  args.push(rawUrl)

  try {
    const proc = deps.spawn(MAC_CHROME_BINARY, args, { detached: true, stdio: 'ignore' })
    proc.on('error', error => deps.log(`[link] chrome spawn failed: ${error.message}`))
    proc.unref()
  } catch (error) {
    deps.log(`[link] chrome spawn threw: ${error instanceof Error ? error.message : String(error)}`)

    return false
  }

  deps.log(`[link] opened in chrome${args.length > 1 ? ` (${args[0]})` : ''}`)

  return true
}

/**
 * The single decision point main.ts binds to `ExternalOpenDeps.openExternal`:
 * Chrome when the user opted in and it can be launched, the OS default
 * handler otherwise.
 */
export async function openWebLink(
  rawUrl: string,
  preferChrome: boolean,
  deps: ChromeOpenDeps,
  fallback: (url: string) => Promise<void>
): Promise<void> {
  if (preferChrome && (await openInChrome(rawUrl, deps))) {
    return
  }

  await fallback(rawUrl)
}
