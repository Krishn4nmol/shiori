import { describe, expect, it } from 'vitest'

import { escapeHtml, findHighlightedItems, normalizeForMatch } from './highlight'

const highlighted = (items: string[], passage: string) =>
  [...findHighlightedItems(items, passage)].map((i) => items[i])

describe('normalizeForMatch', () => {
  it('removes whitespace, lowercases and folds full-width characters', () => {
    expect(normalizeForMatch(' Ａｂｃ　１２３\n de ')).toBe('abc123de')
  })
})

describe('findHighlightedItems', () => {
  const PASSAGE =
    'We measured vegetation cover and grain size at forty sites over three years. ' +
    'Each site was visited monthly and photographed from a fixed point.'

  it('highlights the lines of the passage and nothing else', () => {
    const items = [
      '2 Methods',
      'Sand dunes form where wind carries sand.',
      'We measured vegetation cover and grain size at forty',
      'sites over three years. Each site was visited monthly',
      'and photographed from a fixed point.',
      '3 Results',
      'Cover increased in spring.',
    ]
    expect(highlighted(items, PASSAGE)).toEqual(items.slice(2, 5))
  })

  it('ignores different spacing and a word hyphenated across lines', () => {
    const items = ['We  measured vege-', 'tation cover and grain size at forty sites']
    expect(highlighted(items, PASSAGE)).toEqual(items)
  })

  it('does not highlight short common words found elsewhere on the page', () => {
    // Japanese PDFs often split text into tiny items, and short words appear all over a page.
    const passage =
      '２）植林により飛砂害は激減したが、同時に砂丘内の草原化が課題となった。景観の保全が求められている。'
    const items = [
      '１）海浜植生の個体群構造を定量化し、（',
      'した', // a common ending, also inside the passage, but this item belongs elsewhere
      '）結果を示す。',
      '課題', // appears inside the passage ("課題となった") but this item is a table cell
      '方法',
      '結果',
      '２）植林により飛砂害は激減したが、同時に砂丘内の',
      '草原化が課題となった。景観の保全が求められている。',
    ]
    expect(highlighted(items, passage)).toEqual([items[6], items[7]])
  })

  it('handles text split into tiny items, with furigana merged into the stored passage', () => {
    const passage =
      'しょくせいひふくりつ３）植生被覆率は四十地点で毎月測定した。そくてい…その結果、春に増加した。'
    const items = ['しょくせい', 'ひふくりつ', '３）', '植生被覆率', 'は', '四十地点', 'で', '毎月', '測定した。',
      'そくてい', '…その結果、', '春に', '増加した。', '４）外来植物の除草を行った。']
    const result = highlighted(items, passage)
    expect(result).toContain('植生被覆率')
    expect(result).toContain('四十地点')
    expect(result).toContain('増加した。')
    expect(result).not.toContain('４）外来植物の除草を行った。')
  })

  it('returns nothing for an unrelated page', () => {
    expect(highlighted(['Kidney disease patients were followed for five years.'], PASSAGE)).toEqual([])
  })
})

describe('escapeHtml', () => {
  it('neutralises markup from PDF text', () => {
    expect(escapeHtml('<img src=x onerror="alert(1)">')).toBe(
      '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;',
    )
  })
})
