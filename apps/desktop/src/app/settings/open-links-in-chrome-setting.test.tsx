import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $openLinksInChrome, setOpenLinksInChrome } from '@/store/open-links-in-chrome'

import { OpenLinksInChromeSetting } from './open-links-in-chrome-setting'

const macMock = vi.hoisted(() => ({ isMac: true }))

vi.mock('@/lib/keybinds/combo', () => ({
  get IS_MAC() {
    return macMock.isMac
  }
}))

const TOGGLE = { name: 'Open external links in Google Chrome' }

afterEach(() => {
  cleanup()
  setOpenLinksInChrome(false)
  macMock.isMac = true
})

describe('OpenLinksInChromeSetting', () => {
  it('is off by default and flips the store on macOS', async () => {
    render(<OpenLinksInChromeSetting />)

    const toggle = await screen.findByRole('switch', TOGGLE)

    expect(toggle.getAttribute('aria-checked')).toBe('false')
    expect($openLinksInChrome.get()).toBe(false)

    toggle.click()
    await vi.waitFor(() => expect($openLinksInChrome.get()).toBe(true))
  })

  it('renders nothing off macOS, where there is no Chrome route', () => {
    macMock.isMac = false
    render(<OpenLinksInChromeSetting />)

    expect(screen.queryByRole('switch', TOGGLE)).toBeNull()
  })
})
