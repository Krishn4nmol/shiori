import { describe, expect, it } from 'vitest'

import { answerStages, formatMs } from './stages'

describe('answerStages', () => {
  it('splits generation into waiting and writing so stages add up to the total', () => {
    const timings = { rewrite: 400, retrieval: 3300, first_token: 1200, generation: 1900, total: 5600 }
    const stages = answerStages(timings)
    expect(stages.map((s) => [s.key, s.ms])).toEqual([
      ['rewrite', 400],
      ['retrieval', 3300],
      ['waiting', 1200],
      ['writing', 700],
    ])
    expect(stages.reduce((sum, s) => sum + s.ms, 0)).toBe(timings.total)
  })

  it('omits stages that did not run (no rewrite, no LLM call)', () => {
    expect(answerStages({ retrieval: 3500, total: 3500 }).map((s) => s.key)).toEqual(['retrieval'])
  })
})

describe('formatMs', () => {
  it('uses ms below a second and seconds above', () => {
    expect(formatMs(840)).toBe('840 ms')
    expect(formatMs(5630)).toBe('5.6 s')
    expect(formatMs(null)).toBe('–')
  })
})
