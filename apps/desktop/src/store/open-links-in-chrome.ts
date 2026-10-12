/**
 * Open external links in Google Chrome — a device-local, macOS-only preference.
 *
 * Off (the default): external links go to the OS default browser. On: they go
 * to Chrome, in the profile signed in as the link's `authuser` account when it
 * names one (see electron/chrome-open.ts); a missing Chrome falls back to the
 * OS default. Only matters for links that leave the app — see
 * `$alwaysExternalLinks` for which clicks do.
 *
 * Main owns the behaviour, so the toggle is mirrored to the main process over
 * IPC (same shape as `$disableF12`).
 */

import { atom } from 'nanostores'

import { persistBoolean, storedBoolean } from '@/lib/storage'

const KEY = 'hermes.desktop.openLinksInChrome.v1'

export const $openLinksInChrome = atom<boolean>(typeof window === 'undefined' ? false : storedBoolean(KEY, false))

export function setOpenLinksInChrome(on: boolean): void {
  $openLinksInChrome.set(on)
}

if (typeof window !== 'undefined') {
  $openLinksInChrome.subscribe(on => {
    persistBoolean(KEY, on)
    window.hermesDesktop?.setOpenLinksInChrome?.(on)
  })

  window.addEventListener('storage', event => {
    if (event.key === KEY) {
      $openLinksInChrome.set(storedBoolean(KEY, false))
    }
  })
}
