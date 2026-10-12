import type { LinkSettingsCopy } from './types_link_settings'

export const enLinkSettings: LinkSettingsCopy = {
  alwaysExternalTitle: 'Always open links in external browser',
  alwaysExternalDesc:
    'Open every link you click in your system browser instead of the in-app browser. "Open in in-app browser" in the right-click menu still works.',
  chromeTitle: 'Open external links in Google Chrome',
  chromeDesc:
    'Send links that leave the app to Google Chrome instead of your default browser. A link that names a Google account (authuser=you@example.com) opens in the Chrome profile signed in as that account. Falls back to the default browser if Chrome is not installed.'
}
