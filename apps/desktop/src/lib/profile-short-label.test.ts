import { describe, expect, it } from 'vitest'

import { profileShortLabel } from './profile-short-label'

describe('profileShortLabel', () => {
  it('keeps one mark by default', () => {
    expect(profileShortLabel('bluemile')).toBe('b')
    expect(profileShortLabel('  beacon')).toBe('b')
    expect(profileShortLabel('---')).toBe('?')
  })

  it('uses the first two letters of a single word', () => {
    expect(profileShortLabel('beacon', 2)).toBe('be')
    expect(profileShortLabel('bluemile', 2)).toBe('bl')
  })

  it('uses the initials of the first two words', () => {
    expect(profileShortLabel('marcel-dev', 2)).toBe('md')
    expect(profileShortLabel('park atlas', 2)).toBe('pa')
    expect(profileShortLabel('a_b_c', 2)).toBe('ab')
  })

  it('degrades to the one mark available', () => {
    expect(profileShortLabel('x', 2)).toBe('x')
    expect(profileShortLabel('', 2)).toBe('?')
  })

  it('keeps emoji and combining sequences whole', () => {
    expect(profileShortLabel('👩‍💻 dev', 2)).toBe('👩‍💻d')
    expect(profileShortLabel('éa', 2)).toBe('éa')
  })
})
