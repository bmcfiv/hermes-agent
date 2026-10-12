// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'

import { $sidebarRowMeta, $sidebarWholeFigures, resetSidebarView } from '@/store/layout'

import { SidebarGroupRow } from './chrome'

const totals = { costUsd: 3.42, tokens: 15_400 }

const mount = () => render(<SidebarGroupRow label="Group" lead={null} totals={totals} />)

beforeEach(() => {
  resetSidebarView()
  $sidebarRowMeta.set(['cost', 'tokens'])
})

afterEach(cleanup)

it('shows tenths and cents by default', () => {
  mount()

  expect(screen.getByText('15.4k · $3.42')).toBeTruthy()
})

it('shows whole numbers once the sidebar is set to', () => {
  $sidebarWholeFigures.set(true)
  mount()

  expect(screen.getByText('15k · $3')).toBeTruthy()
})

it('repaints a mounted header when the setting flips', () => {
  mount()
  act(() => $sidebarWholeFigures.set(true))

  expect(screen.getByText('15k · $3')).toBeTruthy()
})
