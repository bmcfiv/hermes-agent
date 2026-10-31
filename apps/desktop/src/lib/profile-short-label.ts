const profileGraphemes = new Intl.Segmenter(undefined, { granularity: 'grapheme' })

const MARK = /[\p{L}\p{N}\p{Extended_Pictographic}\p{Regional_Indicator}]/u
const WORD_BREAK = /[\s\-_./]+/

function markGraphemes(text: string): string[] {
  const out: string[] = []

  for (const { segment } of profileGraphemes.segment(text)) {
    if (MARK.test(segment)) {
      out.push(segment)
    }
  }

  return out
}

/**
 * The rail tile's face. One mark by default; with `count` 2, the initials of the
 * first two words (`marcel-dev` -> `md`), else the first two marks (`bluemile`
 * -> `bl`) so same-initial profiles stay apart. Keeps combining marks and emoji
 * sequences together.
 */
export function profileShortLabel(label: string, count: 1 | 2 = 1): string {
  const text = label.trim()
  const marks = markGraphemes(text)

  if (count === 1 || marks.length < 2) {
    return marks[0] ?? '?'
  }

  const words = text.split(WORD_BREAK).filter(Boolean)
  const initials = words.slice(0, 2).map(word => markGraphemes(word)[0])

  return initials.length === 2 && initials.every(Boolean) ? initials.join('') : marks.slice(0, 2).join('')
}
