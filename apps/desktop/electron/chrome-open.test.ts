import assert from 'node:assert/strict'
import type { ChildProcess } from 'node:child_process'
import { EventEmitter } from 'node:events'

import { test } from 'vitest'

import {
  accountEmailForUrl,
  chromeProfileForEmail,
  MAC_CHROME_BINARY,
  openInChrome,
  openWebLink
} from './chrome-open'

const LOCAL_STATE = JSON.stringify({
  profile: {
    info_cache: {
      Default: { name: 'Person 1', user_name: 'person@example.com' },
      'Profile 2': { name: 'Work', user_name: '' },
      'Profile 5': { name: 'Work Account', user_name: 'work@example.org' }
    }
  }
})

function makeDeps(overrides: Record<string, unknown> = {}) {
  const spawned: Array<{ cmd: string; args: readonly string[] }> = []

  const deps = {
    platform: 'darwin' as NodeJS.Platform,
    homedir: '/home/user',
    exists: () => true,
    readFile: () => LOCAL_STATE,
    spawn: (cmd: string, args: readonly string[]) => {
      spawned.push({ cmd, args })

      const proc = new EventEmitter() as unknown as ChildProcess

      ;(proc as unknown as { unref: () => void }).unref = () => {}

      return proc
    },
    log: () => {},
    ...overrides
  }

  return { deps, spawned }
}

test('reads only an email authuser', () => {
  assert.equal(accountEmailForUrl('https://docs.google.com/d/1/edit?authuser=WORK@Example.org'), 'work@example.org')
  assert.equal(accountEmailForUrl('https://docs.google.com/d/1/edit?authuser=1'), null)
  assert.equal(accountEmailForUrl('https://example.com/'), null)
})

test('resolves a profile by signed-in account, never by display name', () => {
  assert.equal(chromeProfileForEmail(LOCAL_STATE, 'work@example.org'), 'Profile 5')
  assert.equal(chromeProfileForEmail(LOCAL_STATE, 'nobody@example.net'), null)
  assert.equal(chromeProfileForEmail('not json', 'work@example.org'), null)
})

test('opens a Docs link in the Chrome profile signed in as its account', async () => {
  const { deps, spawned } = makeDeps()
  const url = 'https://docs.google.com/document/d/abc/edit?usp=drivesdk&authuser=work@example.org'

  assert.equal(await openInChrome(url, deps), true)
  assert.deepEqual(spawned, [{ cmd: MAC_CHROME_BINARY, args: ['--profile-directory=Profile 5', url] }])
})

test('opens a plain link in Chrome with no profile flag', async () => {
  const { deps, spawned } = makeDeps()

  assert.equal(await openInChrome('https://example.com/x', deps), true)
  assert.deepEqual(spawned[0].args, ['https://example.com/x'])
})

test('declines off macOS, without Chrome, or for non-web schemes', async () => {
  assert.equal(await openInChrome('https://example.com/', makeDeps({ platform: 'linux' }).deps), false)
  assert.equal(await openInChrome('https://example.com/', makeDeps({ exists: () => false }).deps), false)
  assert.equal(await openInChrome('mailto:a@b.com', makeDeps().deps), false)
})

test('declines when spawn throws so the caller falls back to the OS', async () => {
  const { deps } = makeDeps({
    spawn: () => {
      throw new Error('ENOENT')
    }
  })

  assert.equal(await openInChrome('https://example.com/', deps), false)
})

test('openWebLink keeps the OS default while the setting is off', async () => {
  const { deps, spawned } = makeDeps()
  const fallback: string[] = []

  await openWebLink('https://example.com/', false, deps, async url => void fallback.push(url))
  assert.deepEqual(spawned, [])
  assert.deepEqual(fallback, ['https://example.com/'])
})

test('openWebLink uses Chrome when the setting is on', async () => {
  const { deps, spawned } = makeDeps()
  const fallback: string[] = []

  await openWebLink('https://example.com/', true, deps, async url => void fallback.push(url))
  assert.equal(spawned.length, 1)
  assert.deepEqual(fallback, [])
})

test('openWebLink falls back to the OS default when Chrome is absent', async () => {
  const { deps, spawned } = makeDeps({ exists: () => false })
  const fallback: string[] = []

  await openWebLink('https://example.com/', true, deps, async url => void fallback.push(url))
  assert.deepEqual(spawned, [])
  assert.deepEqual(fallback, ['https://example.com/'])
})
