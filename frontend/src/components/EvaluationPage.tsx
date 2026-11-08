import { useCallback, useEffect, useState, type FormEvent } from 'react'

import {
  api,
  type EvalMetrics,
  type EvalQuestion,
  type EvalResultRow,
  type EvalRun,
  type EvalRunIn,
  type ShioriDocument,
  type SweepPoint,
} from '../api'
import { formatMs } from '../lib/stages'

const pct = (v: number | null | undefined) => (v == null ? '–' : `${Math.round(v * 100)}%`)

/** Admin page: build a test set, run it under different settings, compare the numbers. */
export function EvaluationPage({ documents }: { documents: ShioriDocument[] }) {
  const [questions, setQuestions] = useState<EvalQuestion[]>([])
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [compare, setCompare] = useState<string[]>([])
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadQuestions = useCallback(() => api.evalQuestions().then(setQuestions, (e: Error) => setError(e.message)), [])
  const loadRuns = useCallback(() => api.evalRuns().then(setRuns, (e: Error) => setError(e.message)), [])

  useEffect(() => {
    void loadQuestions()
    void loadRuns()
  }, [loadQuestions, loadRuns])

  const running = runs.some((r) => r.status === 'running')
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => void loadRuns(), 2000)
    return () => clearInterval(timer)
  }, [running, loadRuns])

  const compared = runs.filter((r) => compare.includes(r.id) && r.metrics)

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold">Evaluation</h2>
        <p className="text-sm text-stone-500">
          Measure search and answer quality on a fixed set of questions, and compare settings
          before changing them.
        </p>
      </div>
      {error && <p className="text-red-600">{error}</p>}
      <QuestionSet questions={questions} documents={documents} onChange={() => void loadQuestions()} />
      <RunLauncher disabled={running || questions.length === 0} onStarted={() => void loadRuns()} />
      <RunsTable
        runs={runs}
        compare={compare}
        onToggle={(id) => setCompare((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))}
        onOpen={setOpenRun}
        onDelete={async (id) => {
          await api.deleteEvalRun(id)
          setCompare((c) => c.filter((x) => x !== id))
          void loadRuns()
        }}
      />
      {compared.length >= 2 && <CompareTable runs={compared} />}
      {openRun && <RunDrawer id={openRun} onClose={() => setOpenRun(null)} />}
    </div>
  )
}

function Card({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-stone-700">{title}</h3>
        {action}
      </div>
      {children}
    </section>
  )
}

// --- test set -------------------------------------------------------------------------

function QuestionSet({
  questions,
  documents,
  onChange,
}: {
  questions: EvalQuestion[]
  documents: ShioriDocument[]
  onChange: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const titles = Object.fromEntries(documents.map((d) => [d.id, d.title]))
  const answerable = questions.filter((q) => q.expected_answer).length

  async function generate() {
    setBusy(true)
    setNotice(null)
    try {
      const res = await api.generateEvalQuestions(10, 3)
      setNotice(
        `Added ${res.created.length} questions.` +
          (res.skipped.length ? ` Skipped ${res.skipped.length} (the LLM's evidence quote did not match the text).` : '') +
          ' Review them: delete any that are vague or wrong.',
      )
      onChange()
    } catch (err) {
      setNotice((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title={`Test set · ${questions.length} questions (${answerable} answerable, ${questions.length - answerable} should say "not found")`}
      action={
        <div className="flex gap-2 text-sm">
          <button type="button" onClick={() => setAdding(!adding)} className="rounded-lg border border-stone-300 px-3 py-1 hover:bg-stone-50">
            {adding ? 'Close' : '+ Add question'}
          </button>
          <button type="button" onClick={() => void generate()} disabled={busy}
            className="rounded-lg bg-shiori-500 px-3 py-1 font-medium text-white hover:bg-shiori-700 disabled:opacity-50">
            {busy ? 'Generating… (about a minute)' : 'Generate 13 with the LLM'}
          </button>
        </div>
      }
    >
      {notice && <p className="mb-3 rounded-md bg-stone-50 p-2 text-sm text-stone-700">{notice}</p>}
      {adding && <AddQuestionForm documents={documents} onAdded={() => { setAdding(false); onChange() }} />}
      {questions.length === 0 ? (
        <p className="text-sm text-stone-500">
          No test questions yet. Generate some from your papers, then review them. Include a few
          questions your papers do not answer: Shiori should say "not found" for those.
        </p>
      ) : (
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-white text-left text-xs uppercase tracking-wide text-stone-500">
              <tr><th className="py-1.5 pr-3">Question</th><th className="py-1.5 pr-3">Expected</th><th className="py-1.5 pr-3">Evidence</th><th /></tr>
            </thead>
            <tbody>
              {questions.map((q) => (
                <tr key={q.id} className="border-t border-stone-100 align-top">
                  <td className="py-1.5 pr-3">{q.question}<span className="ml-1 text-xs text-stone-400">· {q.source}</span></td>
                  <td className="py-1.5 pr-3 text-stone-600">
                    {q.expected_answer ?? <span className="italic text-stone-400">should say not found</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-xs text-stone-500">
                    {q.evidence ? <>“{q.evidence}”<br />{titles[q.document_id ?? ''] ?? ''}</> : '–'}
                  </td>
                  <td className="py-1.5 text-right">
                    <button type="button" onClick={() => void api.deleteEvalQuestion(q.id).then(onChange)}
                      className="text-xs text-red-600 hover:underline">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

function AddQuestionForm({ documents, onAdded }: { documents: ShioriDocument[]; onAdded: () => void }) {
  const [form, setForm] = useState({ question: '', expected_answer: '', document_id: '', evidence: '' })
  const [error, setError] = useState<string | null>(null)
  const answerable = form.expected_answer.trim() !== ''
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api.addEvalQuestion(
        answerable
          ? { question: form.question, expected_answer: form.expected_answer, document_id: form.document_id || null, evidence: form.evidence }
          : { question: form.question },
      )
      onAdded()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const field = 'w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm outline-none focus:border-shiori-500'
  return (
    <form onSubmit={submit} className="mb-4 space-y-2 rounded-lg bg-stone-50 p-3">
      <input className={field} placeholder="Question (English or 日本語)" value={form.question} onChange={set('question')} required />
      <input className={field} placeholder="Expected answer (leave empty if your papers do NOT answer it)" value={form.expected_answer} onChange={set('expected_answer')} />
      {answerable && (
        <div className="grid gap-2 sm:grid-cols-2">
          <select className={field} value={form.document_id} onChange={set('document_id')} required>
            <option value="">Paper that answers it…</option>
            {documents.filter((d) => d.status === 'ready').map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </select>
          <input className={field} placeholder="Evidence: short exact phrase from the paper" value={form.evidence} onChange={set('evidence')} required />
        </div>
      )}
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button className="rounded-lg bg-stone-800 px-3 py-1.5 text-sm text-white">Add</button>
    </form>
  )
}

// --- runs -----------------------------------------------------------------------------

const PRESETS: { label: string; body: Omit<EvalRunIn, 'name'> }[] = [
  { label: 'Current settings', body: { kind: 'retrieval', mode: 'hybrid', rerank: true } },
  { label: 'No reranker', body: { kind: 'retrieval', mode: 'hybrid', rerank: false } },
  { label: 'Vector only', body: { kind: 'retrieval', mode: 'vector', rerank: true } },
  { label: 'Keyword only', body: { kind: 'retrieval', mode: 'keyword', rerank: true } },
]

function RunLauncher({ disabled, onStarted }: { disabled: boolean; onStarted: () => void }) {
  const [kind, setKind] = useState<'retrieval' | 'answer'>('retrieval')
  const [cutoff, setCutoff] = useState('')
  const [agent, setAgent] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function start(label: string, body: Omit<EvalRunIn, 'name'>) {
    setError(null)
    const min = cutoff.trim() === '' ? null : Number(cutoff)
    const name = `${label}${min != null && body.rerank ? ` · cutoff ${min}` : ''}${kind === 'answer' ? (agent ? ' · answers · agent' : ' · answers · classic') : ''}`
    try {
      await api.startEvalRun({ ...body, kind, name, min_rerank_score: min, agent: kind === 'answer' ? agent : undefined })
      onStarted()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <Card title="Start a run">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={kind === 'retrieval'} onChange={() => setKind('retrieval')} />
          Search only <span className="text-stone-400">(free, ~4 s per question)</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input type="radio" checked={kind === 'answer'} onChange={() => setKind('answer')} />
          Full answers + LLM judge <span className="text-stone-400">(3 LLM calls per question)</span>
        </label>
        {kind === 'answer' && (
          <label className="flex items-center gap-1.5" title="Plan each question first (paper details, search, or read the whole paper)">
            <input type="checkbox" checked={agent} onChange={(e) => setAgent(e.target.checked)} />
            Agent
          </label>
        )}
        <label className="flex items-center gap-1.5">
          Cutoff
          <input value={cutoff} onChange={(e) => setCutoff(e.target.value)} placeholder="default" inputMode="decimal"
            className="w-20 rounded border border-stone-300 px-2 py-0.5" />
        </label>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button key={p.label} type="button" disabled={disabled} onClick={() => void start(p.label, p.body)}
            className="rounded-lg border border-shiori-500 px-3 py-1.5 text-sm text-shiori-700 hover:bg-shiori-50 disabled:opacity-40">
            Run: {p.label}
          </button>
        ))}
      </div>
      {disabled && <p className="mt-2 text-xs text-stone-500">Add test questions, and wait for any run in progress to finish.</p>}
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
    </Card>
  )
}

function RunsTable({
  runs, compare, onToggle, onOpen, onDelete,
}: {
  runs: EvalRun[]
  compare: string[]
  onToggle: (id: string) => void
  onOpen: (id: string) => void
  onDelete: (id: string) => void
}) {
  if (runs.length === 0) return null
  return (
    <Card title="Runs" action={<span className="text-xs text-stone-500">Tick 2 or more to compare</span>}>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-stone-500">
            <tr>
              <th />
              <th className="py-1.5 pr-3">Run</th>
              <th className="py-1.5 pr-2 text-right" title="Right passage ranked first">Hit@1</th>
              <th className="py-1.5 pr-2 text-right" title="Mean reciprocal rank of the right passage">MRR</th>
              <th className="py-1.5 pr-2 text-right" title="Right passage passed the cutoff">Coverage</th>
              <th className="py-1.5 pr-2 text-right" title='Unanswerable questions correctly got "not found"'>Rejection</th>
              <th className="py-1.5 pr-2 text-right" title="LLM judge: answer matches the expected answer">Correct</th>
              <th className="py-1.5 pr-2 text-right">Time</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-t border-stone-100">
                <td className="pr-2"><input type="checkbox" aria-label={`Compare ${r.name}`} checked={compare.includes(r.id)} disabled={!r.metrics} onChange={() => onToggle(r.id)} /></td>
                <td className="py-1.5 pr-3">
                  <button type="button" onClick={() => onOpen(r.id)} className="text-left font-medium text-shiori-700 hover:underline">{r.name}</button>
                  <p className="text-xs text-stone-400">{new Date(r.created_at).toLocaleString()}</p>
                </td>
                {r.status === 'running' ? (
                  <td colSpan={6} className="py-1.5 text-stone-500">
                    Running… {r.progress}/{r.total}
                    <div className="mt-1 h-1.5 w-40 rounded-full bg-stone-100"><div className="h-full rounded-full bg-shiori-500" style={{ width: `${(r.progress / Math.max(r.total, 1)) * 100}%` }} /></div>
                  </td>
                ) : r.status === 'failed' ? (
                  <td colSpan={6} className="py-1.5 text-red-600">Failed: {r.error}</td>
                ) : (
                  <>
                    <td className="pr-2 text-right tabular-nums">{pct(r.metrics?.['hit@1'])}</td>
                    <td className="pr-2 text-right tabular-nums">{r.metrics?.mrr?.toFixed(2) ?? '–'}</td>
                    <td className="pr-2 text-right tabular-nums">{pct(r.metrics?.coverage)}</td>
                    <td className="pr-2 text-right tabular-nums">{pct(r.metrics?.rejection)}</td>
                    <td className="pr-2 text-right tabular-nums">{pct(r.metrics?.correctness)}</td>
                    <td className="pr-2 text-right tabular-nums">{formatMs(r.metrics?.latency_p50_ms)}</td>
                  </>
                )}
                <td className="text-right">
                  {r.status !== 'running' && <button type="button" onClick={() => onDelete(r.id)} className="text-xs text-stone-400 hover:text-red-600">Delete</button>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}

const COMPARE_ROWS: { key: keyof EvalMetrics; label: string; format: (v: number) => string; higherIsBetter: boolean }[] = [
  { key: 'hit@1', label: 'Hit@1: right passage first', format: pct, higherIsBetter: true },
  { key: 'hit@3', label: 'Hit@3', format: pct, higherIsBetter: true },
  { key: 'mrr', label: 'MRR', format: (v) => v.toFixed(2), higherIsBetter: true },
  { key: 'coverage', label: 'Coverage: right passage passed the cutoff', format: pct, higherIsBetter: true },
  { key: 'rejection', label: 'Rejection: unanswerable → "not found"', format: pct, higherIsBetter: true },
  { key: 'correctness', label: 'Correctness (LLM judge)', format: pct, higherIsBetter: true },
  { key: 'faithfulness', label: 'Faithfulness: claims backed by sources', format: pct, higherIsBetter: true },
  { key: 'cited_relevant', label: 'Cited the right passage', format: pct, higherIsBetter: true },
  { key: 'said_not_found', label: 'Unanswerable → said not found', format: pct, higherIsBetter: true },
  { key: 'language_match', label: "Answered in the question's language", format: pct, higherIsBetter: true },
  { key: 'latency_p50_ms', label: 'Time per question (typical)', format: formatMs, higherIsBetter: false },
]

function CompareTable({ runs }: { runs: EvalRun[] }) {
  const rows = COMPARE_ROWS.filter((row) => runs.some((r) => r.metrics?.[row.key] != null))
  return (
    <Card title="Compare runs">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-stone-500">
            <tr><th className="py-1.5 pr-3">Metric</th>{runs.map((r) => <th key={r.id} className="py-1.5 pr-3 text-right font-medium">{r.name}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const values = runs.map((r) => r.metrics?.[row.key] as number | undefined)
              const present = values.filter((v): v is number => v != null)
              const best = row.higherIsBetter ? Math.max(...present) : Math.min(...present)
              return (
                <tr key={row.key} className="border-t border-stone-100">
                  <td className="py-1.5 pr-3 text-stone-600">{row.label}</td>
                  {values.map((v, i) => (
                    <td key={runs[i].id} className={`py-1.5 pr-3 text-right tabular-nums ${v === best && present.length > 1 ? 'font-semibold text-stone-900' : 'text-stone-600'}`}>
                      {v == null ? '–' : row.format(v)}{v === best && present.length > 1 ? ' ★' : ''}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-stone-500">★ best value in each row. With a small test set, differences of one question can look large.</p>
    </Card>
  )
}

// --- run detail -----------------------------------------------------------------------

function RunDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const [run, setRun] = useState<(EvalRun & { results: EvalResultRow[] }) | null>(null)

  useEffect(() => {
    void api.evalRun(id).then(setRun)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [id, onClose])

  return (
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true" aria-label="Evaluation run">
      <button type="button" aria-label="Close" className="absolute inset-0 bg-stone-900/40" onClick={onClose} />
      <div className="relative h-full w-full max-w-4xl space-y-5 overflow-y-auto bg-stone-50 p-6 shadow-2xl">
        <button type="button" onClick={onClose} className="absolute right-4 top-3 text-xl text-stone-500" aria-label="Close">×</button>
        {!run ? <p className="text-stone-500">Loading…</p> : (
          <>
            <header>
              <h2 className="text-lg font-semibold">{run.name}</h2>
              <p className="text-xs text-stone-500">
                {run.config.mode} search · reranker {run.config.rerank ? `on · cutoff ${run.config.min_rerank_score}` : 'off'}
                {run.config.llm_model && ` · ${run.config.llm_model} · prompt ${run.config.prompt_version}`}
              </p>
            </header>
            {run.metrics && run.metrics.sweep.points.length > 0 && (
              <Card title="What if the cutoff were different?">
                <SweepChart points={run.metrics.sweep.points} current={run.config.min_rerank_score} best={run.metrics.sweep.best} />
              </Card>
            )}
            <Card title={`Questions · ${run.results.length}`}>
              <div className="space-y-2">
                {run.results.map((r) => <ResultCard key={r.question_id} r={r} />)}
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  )
}

function ResultCard({ r }: { r: EvalResultRow }) {
  const [open, setOpen] = useState(false)
  const answerable = r.expected_answer != null
  const ok = answerable ? r.kept_relevant : !r.answered
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-3 text-sm">
      <button type="button" onClick={() => setOpen(!open)} className="flex w-full items-start gap-2 text-left">
        <span className={`mt-0.5 shrink-0 rounded px-1.5 text-xs font-medium ${ok ? 'bg-green-50 text-green-800' : 'bg-red-50 text-red-700'}`}>
          {ok ? '✓' : '✗'} {answerable ? (r.relevant_rank ? `found #${r.relevant_rank}${r.kept_relevant ? '' : ', cut'}` : 'not found') : r.answered ? 'answered anyway' : 'said not found'}
        </span>
        <span className="flex-1">{r.question}</span>
        {r.judge?.correctness && <span className="shrink-0 text-xs text-stone-500">judge: {r.judge.correctness}</span>}
      </button>
      {open && (
        <div className="mt-2 space-y-2 border-t border-stone-100 pt-2 text-xs">
          <p><span className="text-stone-500">Expected:</span> {r.expected_answer ?? 'should say not found'}{r.evidence && <> · evidence “{r.evidence}”</>}</p>
          {r.answer && <p><span className="text-stone-500">Answer:</span> {r.answer}</p>}
          {r.judge?.reason && <p className="text-stone-600"><span className="text-stone-500">Judge:</span> {r.judge.reason}</p>}
          {r.error && <p className="text-red-600">{r.error}</p>}
          <table className="w-full">
            <thead className="text-left text-stone-500"><tr><th>Passage</th><th className="text-right">Vec</th><th className="text-right">Kw</th><th className="text-right">Rerank</th><th className="pl-2">Status</th></tr></thead>
            <tbody>
              {r.candidates.slice(0, 8).map((c) => (
                <tr key={c.chunk_id} className={c.relevant ? 'font-medium text-stone-900' : 'text-stone-500'}>
                  <td className="max-w-xs truncate pr-2" title={c.preview}>{c.relevant ? '★ ' : ''}{c.document_title} p.{c.page_start} · {c.preview}</td>
                  <td className="text-right tabular-nums">{c.vector_rank ?? '–'}</td>
                  <td className="text-right tabular-nums">{c.keyword_rank ?? '–'}</td>
                  <td className="text-right tabular-nums">{c.rerank_score != null ? c.rerank_score.toPrecision(2) : '–'}</td>
                  <td className="pl-2">{c.kept ? 'kept' : 'cut'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-stone-400">★ = contains the evidence (relevant)</p>
        </div>
      )}
    </div>
  )
}

const COVERAGE = '#2a78d6' // categorical slot 1
const REJECTION = '#eb6834' // categorical slot 2

/** Coverage and rejection at each cutoff: the trade-off the cutoff controls. */
export function SweepChart({ points, current, best }: { points: SweepPoint[]; current: number; best: SweepPoint | null }) {
  const W = 640, H = 220, L = 44, R = 12, T = 12, B = 40
  const x = (i: number) => L + (i * (W - L - R)) / Math.max(points.length - 1, 1)
  const y = (v: number) => T + (1 - v) * (H - T - B)
  const label = (c: number) => (c === 0 ? '0' : c >= 0.01 ? String(c) : c.toExponential(0).replace('e-', 'e−'))
  const series = [
    { key: 'coverage' as const, name: 'Coverage: real questions keep their answer', color: COVERAGE },
    { key: 'rejection' as const, name: 'Rejection: unanswerable → "not found"', color: REJECTION },
  ].filter((s) => points.some((p) => p[s.key] != null))
  const currentIndex = points.findIndex((p) => Math.abs(p.cutoff - current) < 1e-12)

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Coverage and rejection at each cutoff">
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} stroke="#e7e5e4" strokeWidth={1} />
            <text x={L - 6} y={y(v) + 4} textAnchor="end" fontSize={11} fill="#78716c">{v * 100}%</text>
          </g>
        ))}
        {points.map((p, i) => (
          <text key={p.cutoff} x={x(i)} y={H - B + 16} textAnchor="middle" fontSize={11} fill="#78716c">{label(p.cutoff)}</text>
        ))}
        <text x={(L + W - R) / 2} y={H - 4} textAnchor="middle" fontSize={11} fill="#78716c">relevance cutoff →  stricter</text>
        {currentIndex >= 0 && (
          <g>
            <line x1={x(currentIndex)} x2={x(currentIndex)} y1={T} y2={H - B} stroke="#57534e" strokeWidth={1.5} strokeDasharray="4 3" />
            <text x={x(currentIndex) + 4} y={T + 10} fontSize={11} fill="#57534e">this run</text>
          </g>
        )}
        {series.map((s) => (
          <g key={s.key}>
            <polyline fill="none" stroke={s.color} strokeWidth={2}
              points={points.map((p, i) => `${x(i)},${y(p[s.key] ?? 0)}`).join(' ')} />
            {points.map((p, i) => (
              <g key={p.cutoff}>
                <circle cx={x(i)} cy={y(p[s.key] ?? 0)} r={4} fill={s.color} stroke="#fff" strokeWidth={2} />
                {/* bigger invisible target for the hover tooltip */}
                <circle cx={x(i)} cy={y(p[s.key] ?? 0)} r={10} fill="transparent">
                  <title>{`cutoff ${p.cutoff}: ${s.name.split(':')[0]} ${pct(p[s.key])}`}</title>
                </circle>
              </g>
            ))}
          </g>
        ))}
      </svg>
      <div className="mt-1 flex flex-wrap gap-4 text-xs text-stone-600">
        {series.map((s) => (
          <span key={s.key} className="flex items-center gap-1.5"><span className="h-0.5 w-4" style={{ background: s.color }} />{s.name}</span>
        ))}
      </div>
      {best && (
        <p className="mt-2 text-sm text-stone-700">
          Best balance at cutoff <strong>{best.cutoff}</strong>: coverage {pct(best.coverage)}, rejection {pct(best.rejection)}
          {Math.abs(best.cutoff - current) > 1e-12 ? ` (this run used ${current}).` : ' (the cutoff this run used).'}
        </p>
      )}
      <details className="mt-2 text-xs text-stone-500">
        <summary className="cursor-pointer">Table view</summary>
        <table className="mt-1 w-full max-w-md tabular-nums">
          <thead><tr className="text-left"><th>Cutoff</th><th className="text-right">Coverage</th><th className="text-right">Rejection</th></tr></thead>
          <tbody>{points.map((p) => <tr key={p.cutoff}><td>{p.cutoff}</td><td className="text-right">{pct(p.coverage)}</td><td className="text-right">{pct(p.rejection)}</td></tr>)}</tbody>
        </table>
      </details>
    </div>
  )
}
