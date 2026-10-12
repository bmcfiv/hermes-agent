import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import { createPersistedFlag } from './persisted-flag'

function tempDir() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'persisted-flag-')), 'nested')
}

test('reads off when the file is missing or malformed', () => {
  const dir = tempDir()
  const file = path.join(dir, 'test.json')
  const flag = createPersistedFlag(dir, 'test', () => {})

  assert.equal(flag.read(), false)

  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(file, 'not json')
  assert.equal(flag.read(), false)

  fs.writeFileSync(file, JSON.stringify({ on: 'yes' }))
  assert.equal(flag.read(), false)
})

test('round-trips an explicit on/off, creating the directory', () => {
  const flag = createPersistedFlag(tempDir(), 'test', () => {})

  flag.write(true)
  assert.equal(flag.read(), true)

  flag.write(false)
  assert.equal(flag.read(), false)
})

test('logs, and does not throw, when the file cannot be written', () => {
  const lines: string[] = []
  const dir = tempDir()

  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, 'sub'), 'a file, not a directory')

  createPersistedFlag(path.join(dir, 'sub'), 'test', line => lines.push(line)).write(true)
  assert.equal(lines.length, 1)
  assert.match(lines[0], /^\[test\] write failed/)
})
