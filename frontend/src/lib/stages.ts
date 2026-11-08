/**
 * Splits an answer's timings into sequential stages that add up to the total.
 *
 * The backend records `rewrite` and `retrieval` as durations, then `first_token` and
 * `generation` both measured from the end of retrieval. So generation already contains the
 * wait for the first word: it is split into "waiting" and "writing" so nothing is counted twice.
 */

export type Stage = { key: string; label: string; ms: number }

/** Fixed order = fixed color (categorical slots 1-4); never re-assigned by rank. */
export const STAGES = [
  { key: 'rewrite', label: 'Rewrite follow-up', color: '#2a78d6' },
  { key: 'retrieval', label: 'Search + rerank', color: '#eb6834' },
  { key: 'waiting', label: 'Waiting for first word', color: '#1baf7a' },
  { key: 'writing', label: 'Writing the answer', color: '#eda100' },
] as const

export function answerStages(timings: Record<string, number | undefined>): Stage[] {
  const generation = timings.generation ?? 0
  const waiting = Math.min(timings.first_token ?? 0, generation)
  const values: Record<string, number> = {
    rewrite: timings.rewrite ?? 0,
    retrieval: timings.retrieval ?? 0,
    waiting,
    writing: Math.max(generation - waiting, 0),
  }
  return STAGES.map(({ key, label }) => ({ key, label, ms: values[key] })).filter((s) => s.ms > 0)
}

export const SEARCH_STEPS: Record<string, string> = {
  embed: 'Embed question',
  vector: 'Vector search',
  keyword: 'Keyword search',
  fuse: 'Merge (RRF)',
  rerank: 'Rerank',
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '–'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`
}
