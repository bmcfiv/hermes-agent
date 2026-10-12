// THE compact-number formatter — every user-facing count/token figure goes
// through here. 999 → "999", 1000 → "1k", 1230 → "1.2k", 10000 → "10k",
// 1_500_000 → "1.5M". `M` is the top rung by design: token and cost figures
// this renders stay well under a billion, so 1e9 reads "1000M" rather than
// growing a `B` rung that no surface needs (and that would collide with the
// "bytes" reading in size labels). Do not hand-roll `/ 1000` display math elsewhere.
// `whole: true` drops the tenths — 1230 → "1k", 15_400 → "15k", 1.5M → "2M" —
// for dense surfaces (sidebar figures) where a decimal is noise.
export function compactNumber(value: null | number | undefined, opts?: { whole?: boolean }): string {
  const num = Number(value ?? 0)

  if (!Number.isFinite(num) || num <= 0) {
    return '0'
  }

  const whole = opts?.whole === true

  const scaled = (v: number, suffix: string) =>
    whole ? `${Math.round(v)}${suffix}` : `${v.toFixed(1).replace(/\.0$/, '')}${suffix}`

  // Thresholds sit just under the unit boundary so rounding can't produce
  // "1000k" or "1000" — those promote to the next unit instead.
  if (num >= (whole ? 999_500 : 999_950)) {
    return scaled(num / 1_000_000, 'M')
  }

  if (num >= 999.5) {
    return scaled(num / 1_000, 'k')
  }

  return `${Math.round(num)}`
}
