import { useStore } from '@nanostores/react'

import { useI18n } from '@/i18n'
import { IS_MAC } from '@/lib/keybinds/combo'
import { $openLinksInChrome, setOpenLinksInChrome } from '@/store/open-links-in-chrome'

import { ToggleRow } from './primitives'

/** Advanced ▸ "Open external links in Google Chrome". macOS only: the Chrome route is a no-op elsewhere. */
export function OpenLinksInChromeSetting() {
  const { t } = useI18n()
  const on = useStore($openLinksInChrome)

  if (!IS_MAC) {
    return null
  }

  return (
    <ToggleRow
      checked={on}
      description={t.settings.config.links.chromeDesc}
      label={t.settings.config.links.chromeTitle}
      onChange={setOpenLinksInChrome}
    />
  )
}
