/**
 * Decides which text items of a PDF page belong to a cited passage, so the viewer can
 * highlight them.
 *
 * PDFs split text into items of any size: whole lines, or tiny runs like "（", "した", "）".
 * Asking "does this item appear in the passage?" therefore fails: short common words appear
 * everywhere. Instead the page's items are joined into one string, overlapping windows of the
 * passage are located in it, and an item is highlighted when windows cover most of it. A
 * window is long enough to be specific, so it only matches where the passage really is.
 *
 * Both sides are compared with whitespace removed and full-width characters folded, because
 * the stored passage had its line breaks joined and spacing collapsed.
 */

const WINDOW = 12 // characters per window: specific enough to locate the passage
const STEP = 4 // windows overlap so every part of the passage is tried
const MIN_COVERAGE = 0.6 // share of an item's characters that must be inside matches

export function normalizeForMatch(text: string): string {
  return text.normalize('NFKC').replace(/\s+/g, '').toLowerCase()
}

/** Indexes of the page items that are part of the passage. */
export function findHighlightedItems(items: string[], passage: string): Set<number> {
  const target = normalizeForMatch(passage).replace(/-/g, '')
  const parts = items.map((item) => normalizeForMatch(item).replace(/-/g, ''))
  const page = parts.join('')
  const result = new Set<number>()
  if (!target || !page) return result

  // Mark every page position covered by a window of the passage.
  const covered = new Uint8Array(page.length)
  const size = Math.min(WINDOW, target.length)
  for (let i = 0; i + size <= target.length; i += STEP) {
    const window = target.slice(i, i + size)
    for (let at = page.indexOf(window); at >= 0; at = page.indexOf(window, at + 1)) {
      covered.fill(1, at, at + size)
    }
  }
  // Make sure the passage's last characters are tried too.
  const tail = target.slice(-size)
  for (let at = page.indexOf(tail); at >= 0; at = page.indexOf(tail, at + 1)) {
    covered.fill(1, at, at + size)
  }

  let offset = 0
  parts.forEach((part, index) => {
    if (part.length > 0) {
      let hits = 0
      for (let j = offset; j < offset + part.length; j++) hits += covered[j]
      if (hits / part.length >= MIN_COVERAGE) result.add(index)
    }
    offset += part.length
  })
  return result
}

const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

/** PDF text is untrusted: escape it before it is inserted into the page as HTML. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => ESCAPES[c])
}
