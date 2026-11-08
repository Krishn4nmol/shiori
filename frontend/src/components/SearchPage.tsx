import { useState } from 'react'

import { api, type SearchHit, type SearchMode, type SearchResponse, type ShioriDocument } from '../api'
import { useOpenPdf } from './usePdfViewer'

const EXAMPLES = ['What was measured, and at how many sites?', '鳥取砂丘で何を測定しましたか？']

type Props = { documents: ShioriDocument[] }

export function SearchPage({ documents }: Props) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('hybrid')
  const [rerank, setRerank] = useState(true)
  const [documentId, setDocumentId] = useState('')
  const [result, setResult] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const ready = documents.filter((d) => d.status === 'ready')

  async function run(q: string, showWeak = false) {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      setResult(
        await api.search({
          query: q,
          mode,
          rerank,
          k: 8,
          document_ids: documentId ? [documentId] : undefined,
          min_score: showWeak ? 0 : undefined,
        }),
      )
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-5">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void run(query)
        }}
        className="space-y-3"
      >
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Ask about your papers, in English or 日本語"
            className="min-w-0 flex-1 rounded-lg border border-stone-300 bg-white px-4 py-2.5 shadow-sm outline-none focus:border-shiori-500"
          />
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="rounded-lg bg-shiori-500 px-5 py-2.5 font-medium text-white hover:bg-shiori-700 disabled:opacity-50"
          >
            {loading ? 'Searching…' : 'Search'}
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm text-stone-600">
          <label className="flex items-center gap-2">
            Method
            <select
              value={mode}
              onChange={(e) => setMode(e.target.value as SearchMode)}
              className="rounded border border-stone-300 bg-white px-2 py-1"
            >
              <option value="hybrid">Hybrid (meaning + keywords)</option>
              <option value="vector">Meaning only (vector)</option>
              <option value="keyword">Keywords only</option>
            </select>
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />
            Rerank (more accurate, adds ~3 s)
          </label>
          <label className="flex items-center gap-2">
            Paper
            <select
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
              className="max-w-56 truncate rounded border border-stone-300 bg-white px-2 py-1"
            >
              <option value="">All papers</option>
              {ready.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
          </label>
        </div>

        {!result && (
          <p className="text-sm text-stone-500">
            Try:{' '}
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => {
                  setQuery(ex)
                  void run(ex)
                }}
                className="mr-3 text-shiori-700 hover:underline"
              >
                {ex}
              </button>
            ))}
          </p>
        )}
      </form>

      {error && <p className="text-red-600">{error}</p>}

      {result && (
        <section className="space-y-3">
          <p className="text-xs text-stone-500">
            {result.hits.length} results from {result.candidates} candidates
            {result.filtered_out > 0 && ` (${result.filtered_out} weak matches hidden)`} ·{' '}
            {Object.entries(result.timings_ms)
              .map(([stage, ms]) => `${stage} ${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`)
              .join(' · ')}
          </p>
          {result.hits.length === 0 && (
            <div className="rounded-xl border border-stone-200 bg-white px-6 py-8 text-center">
              <p className="font-medium text-stone-700">
                {result.filtered_out > 0
                  ? 'No passage in your papers answers this.'
                  : 'No matching passages.'}
              </p>
              {result.filtered_out > 0 && result.best_rerank_score !== null && (
                <>
                  <p className="mt-1 text-sm text-stone-500">
                    The closest match scored {result.best_rerank_score.toPrecision(2)}, below the
                    relevance cutoff of {result.min_score}. Try naming the paper or topic, or
                    rephrasing the question.
                  </p>
                  <button
                    type="button"
                    onClick={() => void run(result.query, true)}
                    className="mt-3 text-sm font-medium text-shiori-700 hover:underline"
                  >
                    Show the {result.filtered_out} weak matches anyway
                  </button>
                </>
              )}
            </div>
          )}
          {result.hits.map((hit, i) => (
            <HitCard key={hit.chunk_id} hit={hit} rank={i + 1} />
          ))}
        </section>
      )}
    </div>
  )
}

function HitCard({ hit, rank }: { hit: SearchHit; rank: number }) {
  const openPdf = useOpenPdf()
  const [open, setOpen] = useState(false)
  const pages = hit.page_start === hit.page_end ? `p. ${hit.page_start}` : `pp. ${hit.page_start}–${hit.page_end}`

  return (
    <article className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-xs text-stone-500">
            #{rank} · <span className="font-medium text-stone-700">{hit.document_title}</span>
          </p>
          <p className="mt-0.5 text-sm text-stone-600">
            {hit.section ?? 'No section'} ·{' '}
            <button
              type="button"
              onClick={() =>
                openPdf({
                  documentId: hit.document_id,
                  title: hit.document_title,
                  page: hit.page_start,
                  pageEnd: hit.page_end,
                  highlight: hit.text,
                })
              }
              className="text-shiori-700 hover:underline"
              title="Open the PDF with this passage highlighted"
            >
              {pages} ↗
            </button>{' '}
            · <span className="uppercase">{hit.language}</span>
          </p>
        </div>
        <ScoreBadges hit={hit} />
      </header>
      <p
        className={`mt-2 whitespace-pre-line text-sm leading-relaxed text-stone-800 ${open ? '' : 'line-clamp-4'}`}
      >
        {hit.text}
      </p>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="mt-1 text-xs text-stone-500 hover:underline"
      >
        {open ? 'Show less' : 'Show full passage'}
      </button>
    </article>
  )
}

function ScoreBadges({ hit }: { hit: SearchHit }) {
  const badge = 'rounded px-1.5 py-0.5 font-mono'
  return (
    <div className="flex shrink-0 flex-wrap justify-end gap-1 text-[11px]">
      {hit.rerank_score !== null && (
        <span className={`${badge} bg-shiori-50 text-shiori-700`} title="Reranker relevance (0–1)">
          rerank {hit.rerank_score.toPrecision(2)}
        </span>
      )}
      <span
        className={`${badge} bg-sky-50 text-sky-700`}
        title={hit.vector_score !== null ? `cosine similarity ${hit.vector_score}` : 'not found by vector search'}
      >
        vec {hit.vector_rank ? `#${hit.vector_rank}` : '–'}
      </span>
      <span
        className={`${badge} bg-violet-50 text-violet-700`}
        title={hit.keyword_score !== null ? `keyword rank score ${hit.keyword_score}` : 'no keyword match'}
      >
        kw {hit.keyword_rank ? `#${hit.keyword_rank}` : '–'}
      </span>
    </div>
  )
}
