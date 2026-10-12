import { compactNumber } from '@hermes/shared'

import type { SidebarRowMeta } from '@/store/layout'
import { formatSidebarCost } from '@/store/sidebar-archive'

/** The token and spend figures a sidebar row or group header shows, per the
 *  Show menu. Tokens and cost share one figure rather than each claiming a
 *  column: several switched on read as one number, not as a widening gutter. */
export function sidebarFigures(rowMeta: SidebarRowMeta[], tokens: number, costUsd: number, whole: boolean): string[] {
  return [
    rowMeta.includes('tokens') && tokens > 0 ? compactNumber(tokens, { whole }) : null,
    // Sub-cent spend rounds to "$0.00", which reads as a bug rather than as a
    // cheap session — below a cent nothing is shown at all.
    rowMeta.includes('cost') && costUsd >= 0.01 ? formatSidebarCost(costUsd, whole) : null
  ].filter(Boolean) as string[]
}
