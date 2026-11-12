// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { setApiRequestConnection } from '@/api/client'
import { SidebarProvider } from '@/components/ui/sidebar'
import type { HermesConnection } from '@/global'
import { $connectionsRegistry } from '@/store/connection-registry-state'
import { setPrimaryGatewayConnection } from '@/store/gateway'
import { $pinnedSessionIds, setSidebarAgentsGrouped, setSidebarGrouping } from '@/store/layout'
import { $activeGatewayProfile, $profiles, setShowAllProfiles } from '@/store/profile'
import { $connection, $sessions, $sessionsLoading, mergeSessionPage } from '@/store/session'
import { makeSessionInfo } from '@/test/session-info'

import { ChatSidebar } from './index'

const noop = () => {}

const noopAsync = async () => {}

const mount = () =>
  render(
    <MemoryRouter>
      <SidebarProvider>
        <ChatSidebar
          currentView="chat"
          onArchiveSession={noop}
          onBranchSession={noop}
          onDeleteSession={noop}
          onLoadMoreSessions={noop}
          onManageCronJob={noop}
          onNavigate={noop}
          onNewSessionInWorkspace={noop}
          onNewSessionSplit={noop}
          onResumeSession={noop}
          onRetrySessions={noopAsync}
          onTriggerCronJob={noopAsync}
        />
      </SidebarProvider>
    </MemoryRouter>
  )

// Three gateways that each serve a `default` profile: this computer (the registry
// primary, whose rows are never tagged) and two remote machines.
const registry = {
  version: 2,
  primary: 'local',
  secureTokenStorage: true,
  connections: [
    { id: 'local', label: 'This computer', kind: 'local', tokenSet: false, tokenPreview: null },
    { id: 'homelab', label: 'Homelab', kind: 'remote', tokenSet: true, tokenPreview: null },
    { id: 'office', label: 'Office', kind: 'remote', tokenSet: true, tokenPreview: null }
  ]
} as NonNullable<typeof $connectionsRegistry.value>

// What the window held while Office was in front: a running chat (its turn keeps it in the keep set).
const officeRunning = makeSessionInfo({
  connection_id: 'office',
  cwd: '/srv/projects/app',
  id: 'office-running',
  last_active: 90,
  profile: 'default',
  title: 'Redesign GUI'
})

const officeIdle = makeSessionInfo({
  connection_id: 'office',
  id: 'office-idle',
  last_active: 80,
  profile: 'default',
  title: 'Office idle chat'
})

// A chat this computer owns that was open when the window left it (untagged: the primary pool).
const hubOpen = makeSessionInfo({ id: 'hub-open', last_active: 70, profile: 'default', title: 'Laptop chat' })

const homelabOwn = makeSessionInfo({
  connection_id: 'homelab',
  id: 'homelab-own',
  last_active: 60,
  profile: 'default',
  title: 'Family history'
})

/** The list after the user picks Homelab while Office's chat is still running: the
 *  refresh merges Homelab's page over what was loaded and keeps the working / open rows. */
const listAfterPickingHomelab = () =>
  mergeSessionPage([officeRunning, officeIdle, hubOpen], [homelabOwn], ['office-running', 'hub-open'])

const front = (connectionId: string, mode: 'local' | 'remote') => {
  $connection.set({ connectionId, mode } as unknown as HermesConnection)
  setApiRequestConnection(connectionId === 'local' ? null : connectionId)
}

describe('ChatSidebar: another gateway\'s rows', () => {
  beforeEach(() => {
    $connectionsRegistry.set(registry)
    setPrimaryGatewayConnection({ connectionId: 'local', mode: 'local' } as unknown as HermesConnection)
    $profiles.set([{ name: 'default', is_default: true }] as typeof $profiles.value)
    $activeGatewayProfile.set('default')
    $pinnedSessionIds.set([])
    $sessionsLoading.set(false)
    setShowAllProfiles(false)
    setSidebarGrouping('date')
    setSidebarAgentsGrouped(false)
  })

  afterEach(() => {
    cleanup()
    $sessions.set([])
    $connection.set(null)
    setPrimaryGatewayConnection(null)
    setApiRequestConnection(null)
    $profiles.set([])
    $connectionsRegistry.set(null)
  })

  it('the refresh after a gateway pick still holds the previous gateway\'s running chat', () => {
    // The store keeps it on purpose (an open tab must still resolve its owner); the list must not show it.
    expect(listAfterPickingHomelab().map(session => session.id)).toEqual(['office-running', 'hub-open', 'homelab-own'])
  })

  it('lists only the window gateway\'s chats under its profile', () => {
    front('homelab', 'remote')
    $sessions.set(listAfterPickingHomelab())
    mount()

    expect(screen.getByText('Family history')).toBeTruthy()
    // Office's running chat is not also "running" on Homelab…
    expect(screen.queryByText('Redesign GUI')).toBeNull()
    // …and neither is the chat this computer owns.
    expect(screen.queryByText('Laptop chat')).toBeNull()
  })

  it('shows the same chat again once its own gateway is in front', () => {
    front('office', 'remote')
    $sessions.set([officeRunning, officeIdle, homelabOwn, hubOpen])
    mount()

    expect(screen.getByText('Redesign GUI')).toBeTruthy()
    expect(screen.getByText('Office idle chat')).toBeTruthy()
    expect(screen.queryByText('Family history')).toBeNull()
    expect(screen.queryByText('Laptop chat')).toBeNull()
  })

  it('keeps this computer\'s untagged chats when it is the gateway in front', () => {
    front('local', 'local')
    $sessions.set([hubOpen, officeRunning, homelabOwn])
    mount()

    expect(screen.getByText('Laptop chat')).toBeTruthy()
    expect(screen.queryByText('Redesign GUI')).toBeNull()
    expect(screen.queryByText('Family history')).toBeNull()
  })

  it('hides nothing when the window cannot name its gateway yet', () => {
    $connection.set(null)
    setApiRequestConnection(null)
    $sessions.set([hubOpen, homelabOwn])
    mount()

    expect(screen.getByText('Laptop chat')).toBeTruthy()
    expect(screen.getByText('Family history')).toBeTruthy()
  })
})
