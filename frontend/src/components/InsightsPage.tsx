import { useCallback, useEffect, useState } from 'react'

import {
  api,
  type InsightsSummary,
  type TraceDetail,
  type TraceFilter,
  type TraceRow,
} from '../api'
import { answerStages, formatMs, SEARCH_STEPS, STAGES } from '../lib/stages'

const RANGES = [1, 7, 30]

/** Admin dashboard: answer quality, speed and cost, and a trace for every question. */
export function InsightsPage() {
  const [days, setDays] = useState(7)
  const [summary, setSummary] = useState<InsightsSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openTrace, setOpenTrace] = useState<string | null>(null)

  useEffect(() => {
    api.insightsSummary(days).then(setSummary, (err: Error) => setError(err.message))
  }, [days])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Insights</h2>
        <div className="flex rounded-lg border border-stone-200 bg-white p-0.5 text-sm" role="group" aria-label="Time range">
          {RANGES.map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1 ${days === d ? 'bg-shiori-50 font-medium text-shiori-700' : 'text-stone-600'}`}
            >
              {d === 1 ? 'Today' : `${d} days`}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      {summary && <SummaryView s={summary} />}
      <TraceList onOpen={setOpenTrace} />
      {openTrace && <TraceDrawer id={openTrace} onClose={() => setOpenTrace(null)} />}
      <p className="text-xs text-stone-400">
        Admins can see the questions asked so they can find and fix weak answers. Conversations
        themselves stay private on the Ask page.
      </p>
    </div>
  )
}

// --- summary ------------------------------------------------------------------------

function SummaryView({ s }: { s: InsightsSummary }) {
  const pct = (v: number | null) => (v == null ? '–' : `${Math.round(v * 100)}%`)
  const stages = answerStages(s.stage_avg_ms)
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Questions" value={String(s.questions)} note={`${s.searches} searches`} />
        <Tile
          label="Answered"
          value={pct(s.answered_rate)}
          note={s.error_rate ? `${pct(s.error_rate)} errors` : 'rest: "not found"'}
        />
        <Tile
          label="Time to answer"
          value={formatMs(s.latency_ms.p50)}
          note={`typical · slowest 5%: ${formatMs(s.latency_ms.p95)}`}
        />
        <Tile
          label="Cost"
          value={`$${s.cost_usd.toFixed(4)}`}
          note={`${(s.input_tokens + s.output_tokens).toLocaleString()} tokens · 👍 ${s.feedback_up} 👎 ${s.feedback_down}`}
        />
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Where the time goes (average answer)">
          {stages.length ? (
            <>
              <StackedBar stages={stages} />
              <Legend stages={stages} />
              {Object.keys(s.search_stage_avg_ms).length > 0 && (
                <p className="mt-3 text-xs text-stone-500">
                  Inside search:{' '}
                  {Object.entries(s.search_stage_avg_ms)
                    .map(([k, ms]) => `${SEARCH_STEPS[k] ?? k} ${formatMs(ms)}`)
                    .join(' · ')}
                </p>
              )}
              <p className="mt-1 text-xs text-stone-500">
                First word appears after {formatMs(s.first_token_ms.p50)} (typical)
              </p>
            </>
          ) : (
            <Empty />
          )}
        </Card>
        <Card title="Questions per day">
          {s.per_day.length ? <DayBars days={s.per_day} /> : <Empty />}
        </Card>
      </div>

      {s.unanswered_questions.length > 0 && (
        <Card title="Recent questions nothing in the library answered">
          <ul className="list-disc space-y-1 pl-5 text-sm text-stone-700">
            {s.unanswered_questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-stone-500">
            Gaps in the library, or questions worded differently from the papers.
          </p>
        </Card>
      )}
    </>
  )
}

function Tile({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-stone-900">{value}</p>
      <p className="mt-0.5 text-xs text-stone-500">{note}</p>
    </div>
  )
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-stone-700">{title}</h3>
      {children}
    </section>
  )
}

const Empty = () => <p className="text-sm text-stone-400">No questions in this period yet.</p>

const colorOf = (key: string) => STAGES.find((s) => s.key === key)?.color ?? '#a8a29e'

/** One horizontal bar, segments in fixed stage order, 2px surface gaps, rounded ends. */
function StackedBar({ stages }: { stages: { key: string; label: string; ms: number }[] }) {
  const total = stages.reduce((sum, s) => sum + s.ms, 0)
  return (
    <div className="flex h-4 w-full gap-[2px]" role="img" aria-label={stages.map((s) => `${s.label} ${formatMs(s.ms)}`).join(', ')}>
      {stages.map((s, i) => (
        <div
          key={s.key}
          title={`${s.label}: ${formatMs(s.ms)} (${Math.round((s.ms / total) * 100)}%)`}
          style={{ width: `${(s.ms / total) * 100}%`, background: colorOf(s.key) }}
          className={`h-full min-w-[3px] ${i === 0 ? 'rounded-l' : ''} ${i === stages.length - 1 ? 'rounded-r' : ''}`}
        />
      ))}
    </div>
  )
}

function Legend({ stages }: { stages: { key: string; label: string; ms: number }[] }) {
  return (
    <ul className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-stone-600">
      {stages.map((s) => (
        <li key={s.key} className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorOf(s.key) }} />
          <span className="truncate">{s.label}</span>
          <span className="ml-auto tabular-nums text-stone-800">{formatMs(s.ms)}</span>
        </li>
      ))}
    </ul>
  )
}

const ANSWERED = '#2a78d6' // categorical slot 1
const UNANSWERED = '#eb6834' // categorical slot 2

function DayBars({ days }: { days: { day: string; questions: number; unanswered: number }[] }) {
  const max = Math.max(...days.map((d) => d.questions), 1)
  return (
    <div>
      <div className="flex h-32 items-end gap-1.5 border-b border-stone-200">
        {days.map((d) => {
          const answered = d.questions - d.unanswered
          return (
            <div
              key={d.day}
              className="flex h-full flex-1 flex-col justify-end gap-[2px]"
              title={`${d.day}: ${d.questions} questions, ${answered} answered, ${d.unanswered} not found`}
            >
              {d.unanswered > 0 && (
                <div className="rounded-t" style={{ height: `${(d.unanswered / max) * 100}%`, background: UNANSWERED }} />
              )}
              {answered > 0 && (
                <div
                  className={d.unanswered > 0 ? '' : 'rounded-t'}
                  style={{ height: `${(answered / max) * 100}%`, background: ANSWERED }}
                />
              )}
            </div>
          )
        })}
      </div>
      <div className="mt-1 flex gap-1.5 text-[10px] text-stone-400">
        {days.map((d) => (
          <span key={d.day} className="flex-1 truncate text-center">
            {d.day.slice(5)}
          </span>
        ))}
      </div>
      <div className="mt-2 flex gap-4 text-xs text-stone-600">
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: ANSWERED }} />Answered</span>
        <span className="flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: UNANSWERED }} />Not found</span>
        <span className="ml-auto text-stone-400">max {max}/day</span>
      </div>
    </div>
  )
}

// --- trace list -----------------------------------------------------------------------

const FILTERS: { value: TraceFilter | ''; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'unanswered', label: 'Not found' },
  { value: 'negative', label: '👎 rated' },
  { value: 'errors', label: 'Errors' },
]

function TraceList({ onOpen }: { onOpen: (id: string) => void }) {
  const [only, setOnly] = useState<TraceFilter | ''>('')
  const [kind, setKind] = useState<'chat' | 'search' | ''>('chat')
  const [page, setPage] = useState<{ total: number; items: TraceRow[] }>({ total: 0, items: [] })
  const [offset, setOffset] = useState(0)

  const load = useCallback(async () => {
    setPage(await api.listTraces({ kind: kind || undefined, only: only || undefined, offset }))
  }, [kind, only, offset])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <Card title="Traces">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <select value={kind} onChange={(e) => { setKind(e.target.value as typeof kind); setOffset(0) }}
          className="rounded border border-stone-300 bg-white px-2 py-1">
          <option value="chat">Questions (Ask)</option>
          <option value="search">Searches</option>
          <option value="">Both</option>
        </select>
        {FILTERS.map((f) => (
          <button key={f.value} type="button" onClick={() => { setOnly(f.value); setOffset(0) }}
            className={`rounded-full border px-3 py-0.5 ${only === f.value ? 'border-shiori-500 bg-shiori-50 text-shiori-700' : 'border-stone-200 text-stone-600'}`}>
            {f.label}
          </button>
        ))}
        <span className="ml-auto text-xs text-stone-500">{page.total} traces</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
            <tr>
              <th className="py-1.5 pr-3">When</th>
              <th className="py-1.5 pr-3">Question</th>
              <th className="py-1.5 pr-3">Result</th>
              <th className="py-1.5 pr-3 text-right">Kept</th>
              <th className="py-1.5 pr-3 text-right">Time</th>
              <th className="py-1.5 text-right">Cost</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((t) => (
              <tr key={t.id} onClick={() => onOpen(t.id)} className="cursor-pointer border-t border-stone-100 hover:bg-stone-50">
                <td className="whitespace-nowrap py-1.5 pr-3 text-stone-500">
                  {new Date(t.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
                </td>
                <td className="max-w-xs truncate py-1.5 pr-3" title={t.query}>
                  {t.query}
                  {t.user_name && <span className="ml-1 text-xs text-stone-400">· {t.user_name}</span>}
                </td>
                <td className="whitespace-nowrap py-1.5 pr-3"><ResultBadge t={t} /></td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-stone-600">
                  {t.n_candidates ? `${Math.max(Math.min(t.n_candidates, 12) - t.filtered_out, 0)}/${Math.min(t.n_candidates, 12)}` : '–'}
                </td>
                <td className="py-1.5 pr-3 text-right tabular-nums">{formatMs(t.latency_ms)}</td>
                <td className="py-1.5 text-right tabular-nums text-stone-600">${t.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
            {page.items.length === 0 && (
              <tr><td colSpan={6} className="py-6 text-center text-stone-400">No traces match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {page.total > 25 && (
        <div className="mt-3 flex justify-end gap-2 text-sm">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(offset - 25)} className="rounded px-2 py-1 hover:bg-stone-100 disabled:opacity-30">‹ Newer</button>
          <button type="button" disabled={offset + 25 >= page.total} onClick={() => setOffset(offset + 25)} className="rounded px-2 py-1 hover:bg-stone-100 disabled:opacity-30">Older ›</button>
        </div>
      )}
    </Card>
  )
}

function ResultBadge({ t }: { t: TraceRow }) {
  const badge = 'rounded-full px-2 py-0.5 text-xs font-medium'
  if (t.error) return <span className={`${badge} bg-red-50 text-red-700`}>⚠ error</span>
  if (!t.answered) return <span className={`${badge} bg-amber-50 text-amber-800`}>∅ not found</span>
  return (
    <span className={`${badge} bg-green-50 text-green-800`}>
      ✓ answered{t.feedback === 1 ? ' · 👍' : t.feedback === -1 ? ' · 👎' : ''}
    </span>
  )
}

// --- trace detail ---------------------------------------------------------------------

function TraceDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [trace, setTrace] = useState<TraceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getTrace(id).then(setTrace, (err: Error) => setError(err.message))
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id, onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Trace">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-stone-900/40" onClick={onClose} />
      <div className="relative h-full w-full max-w-3xl overflow-y-auto bg-stone-50 p-6 shadow-2xl">
        <button type="button" onClick={onClose} className="absolute right-4 top-3 text-xl text-stone-500" aria-label="Close">×</button>
        {error && <p className="text-red-600">{error}</p>}
        {!trace ? <p className="text-stone-500">Loading…</p> : <TraceBody t={trace} />}
      </div>
    </div>
  )
}

function TraceBody({ t }: { t: TraceDetail }) {
  const stages = t.kind === 'chat' ? answerStages(t.timings_ms) : []
  const search = Object.entries(t.timings_ms)
    .map(([k, ms]) => [k.replace(/^search_/, ''), ms] as const)
    .filter(([k]) => k in SEARCH_STEPS)
  return (
    <div className="space-y-5 text-sm">
      <header>
        <p className="text-xs text-stone-500">
          {t.kind === 'chat' ? 'Question' : 'Search'} · {new Date(t.created_at).toLocaleString()} · {t.user_name ?? 'unknown user'}
        </p>
        <h2 className="mt-1 text-lg font-semibold">{t.query}</h2>
        {t.search_query && t.search_query !== t.query && (
          <p className="text-stone-600">Searched as: “{t.search_query}”</p>
        )}
        <p className="mt-2"><ResultBadge t={t} /></p>
        {t.error && <p className="mt-2 rounded bg-red-50 p-2 text-red-700">{t.error}</p>}
      </header>

      {t.plan && (
        <section className="rounded-lg border border-stone-200 bg-white p-3">
          <h3 className="mb-1 font-semibold">
            Agent plan · {t.plan.question_type}
            {t.plan.fallback && <span className="ml-2 text-xs font-normal text-amber-700">planner reply unusable: plain search used</span>}
          </h3>
          <p className="text-stone-600">Understood as: “{t.plan.question}”</p>
          {t.plan.queries.length > 0 && <p className="text-xs text-stone-500">Queries: {t.plan.queries.map((q) => `“${q}”`).join(' · ')}</p>}
          {t.plan.papers.length > 0 && <p className="text-xs text-stone-500">Papers: {t.plan.papers.map((p) => p.title).join(', ')}</p>}
          <ul className="mt-1 list-disc pl-5 text-xs text-stone-600">{t.plan.steps.map((s) => <li key={s}>{s}</li>)}</ul>
        </section>
      )}

      <section>
        <h3 className="mb-2 font-semibold">Timing · {formatMs(t.latency_ms)}</h3>
        {stages.length > 0 && <Waterfall stages={stages} total={t.latency_ms} />}
        {search.length > 0 && (
          <p className="mt-2 text-xs text-stone-500">
            Search steps: {search.map(([k, ms]) => `${SEARCH_STEPS[k]} ${formatMs(ms)}`).join(' · ')}
          </p>
        )}
        {t.model && (
          <p className="mt-1 text-xs text-stone-500">
            {t.model} · {t.input_tokens.toLocaleString()} in / {t.output_tokens.toLocaleString()} out tokens · ${t.cost_usd.toFixed(5)}
          </p>
        )}
      </section>

      <section>
        <h3 className="mb-2 font-semibold">
          Passages considered · {t.candidates.filter((c) => c.kept).length} kept of {t.candidates.length}
        </h3>
        <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
          <table className="w-full text-xs">
            <thead className="bg-stone-50 text-left uppercase tracking-wide text-stone-500">
              <tr>
                <th className="px-2 py-1.5">Passage</th>
                <th className="px-2 py-1.5 text-right" title="Rank in vector (meaning) search">Vec</th>
                <th className="px-2 py-1.5 text-right" title="Rank in keyword search">Kw</th>
                <th className="px-2 py-1.5 text-right" title="Reranker relevance, 0-1">Rerank</th>
                <th className="px-2 py-1.5">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {t.candidates.map((c) => (
                <tr key={c.chunk_id} className={`border-t border-stone-100 ${c.kept ? '' : 'text-stone-400'}`}>
                  <td className="max-w-sm px-2 py-1.5">
                    <p className="truncate font-medium" title={c.document_title}>{c.document_title} · p. {c.page_start}</p>
                    <p className="line-clamp-2" title={c.preview}>{c.preview}</p>
                  </td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{c.vector_rank ?? '–'}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{c.keyword_rank ?? '–'}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums">{c.rerank_score != null ? c.rerank_score.toPrecision(2) : '–'}</td>
                  <td className="whitespace-nowrap px-2 py-1.5">
                    {c.cited ? '✓ cited' : c.kept ? 'kept, not cited' : '✗ below cutoff'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {t.answer != null && (
        <section>
          <h3 className="mb-2 font-semibold">Answer</h3>
          <p className="whitespace-pre-wrap rounded-lg border border-stone-200 bg-white p-3">{t.answer || '(empty)'}</p>
          {t.feedback_note && <p className="mt-1 text-xs text-stone-600">👎 note: “{t.feedback_note}”</p>}
        </section>
      )}

      {t.kind === 'chat' && (
        <details className="rounded-lg border border-stone-200 bg-white p-3">
          <summary className="cursor-pointer font-semibold">System prompt · version {t.prompt_version}</summary>
          {t.system_prompt ? (
            <pre className="mt-2 whitespace-pre-wrap text-xs text-stone-700">{t.system_prompt}</pre>
          ) : (
            <p className="mt-2 text-xs text-stone-500">This answer used an older prompt version.</p>
          )}
          <p className="mt-2 text-xs text-stone-500">
            The model received this prompt, the numbered passages marked “kept” above, and the question.
          </p>
        </details>
      )}
    </div>
  )
}

/** Each stage as its own bar, offset by when it started: shows order and duration together. */
function Waterfall({ stages, total }: { stages: { key: string; label: string; ms: number }[]; total: number }) {
  const span = Math.max(total, stages.reduce((sum, s) => sum + s.ms, 0))
  // Each stage starts where the previous ones ended.
  const starts = stages.map((_, i) => stages.slice(0, i).reduce((sum, s) => sum + s.ms, 0))
  return (
    <div className="space-y-1.5">
      {stages.map((s, i) => {
        const left = (starts[i] / span) * 100
        return (
          <div key={s.key} className="grid grid-cols-[10rem_1fr_4rem] items-center gap-2 text-xs">
            <span className="flex items-center gap-1.5 text-stone-600">
              <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: colorOf(s.key) }} />
              {s.label}
            </span>
            <div className="relative h-3 rounded bg-stone-100">
              <div
                className="absolute inset-y-0 rounded"
                style={{ left: `${left}%`, width: `max(${(s.ms / span) * 100}%, 3px)`, background: colorOf(s.key) }}
                title={`${s.label}: ${formatMs(s.ms)}`}
              />
            </div>
            <span className="text-right tabular-nums text-stone-800">{formatMs(s.ms)}</span>
          </div>
        )
      })}
    </div>
  )
}
