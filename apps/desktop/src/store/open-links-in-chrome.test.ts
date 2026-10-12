import { afterEach, describe, expect, it, vi } from 'vitest'

import { storedBoolean } from '@/lib/storage'

import { $openLinksInChrome, setOpenLinksInChrome } from './open-links-in-chrome'

const KEY = 'hermes.desktop.openLinksInChrome.v1'

afterEach(() => {
  setOpenLinksInChrome(false)
  delete (window as { hermesDesktop?: unknown }).hermesDesktop
})

describe('open-links-in-chrome store', () => {
  it('defaults off and persists the pref', () => {
    expect($openLinksInChrome.get()).toBe(false)

    setOpenLinksInChrome(true)
    expect(storedBoolean(KEY, false)).toBe(true)

    setOpenLinksInChrome(false)
    expect(storedBoolean(KEY, true)).toBe(false)
  })

  it('mirrors the toggle to the main process', () => {
    const setter = vi.fn()

    ;(window as unknown as { hermesDesktop: unknown }).hermesDesktop = { setOpenLinksInChrome: setter }

    setOpenLinksInChrome(true)
    expect(setter).toHaveBeenLastCalledWith(true)
  })

  it('follows a change made in another window', () => {
    window.localStorage.setItem(KEY, 'true')
    window.dispatchEvent(new StorageEvent('storage', { key: KEY }))
    expect($openLinksInChrome.get()).toBe(true)
  })
})
