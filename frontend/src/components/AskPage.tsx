import { useCallback, useEffect, useRef, useState } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'

import { useOpenPdf } from './usePdfViewer'

import {
  api,
  streamChat,
  type AgentPlan,
  type ChatMessage,
  type Conversation,
  type ShioriDocument,
  type Source,
} from '../api'

const CITATION = /[[［](\d+(?:\s*[,，、]\s*\d+)*)[\]］]/g
const EXAMPLES = ['What problem does this paper solve, and how?', 'この論文の主な結論は何ですか？']

type UiMessage = Partial<ChatMessage> & {
  key: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  plan?: AgentPlan
}

type Props = { documents: ShioriDocument[] }

export function AskPage({ documents }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  const loadConversations = useCallback(async () => {
    try {
      setConversations(await api.listConversations())
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    void loadConversations()
  }, [loadConversations])

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  async function openConversation(id: string) {
    abort.current?.abort()
    setError(null)
    try {
      const detail = await api.getConversation(id)
      setActiveId(id)
      setMessages(detail.messages.map((m) => ({ ...m, key: m.id })))
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function newConversation() {
    abort.current?.abort()
    setActiveId(null)
    setMessages([])
    setError(null)
  }

  async function removeConversation(id: string) {
    if (!confirm('Delete this conversation? This cannot be undone.')) return
    await api.deleteConversation(id)
    if (id === activeId) newConversation()
    void loadConversations()
  }

  function patchLast(update: (m: UiMessage) => UiMessage) {
    setMessages((prev) => [...prev.slice(0, -1), update(prev[prev.length - 1])])
  }

  async function send(question: string) {
    const content = question.trim()
    if (!content || busy) return
    setInput('')
    setError(null)
    setBusy(true)
    const now = Date.now()
    setMessages((prev) => [
      ...prev,
      { key: `u${now}`, role: 'user', content },
      { key: `a${now}`, role: 'assistant', content: '', streaming: true },
    ])
    const controller = new AbortController()
    abort.current = controller
    try {
      for await (const { event, data } of streamChat(
        {
          content,
          conversation_id: activeId ?? undefined,
          document_ids: documentId ? [documentId] : undefined,
        },
        controller.signal,
      )) {
        if (event === 'conversation') {
          if (!activeId) {
            setActiveId(data.id)
            void loadConversations()
          }
        } else if (event === 'plan') {
          patchLast((m) => ({ ...m, plan: data }))
        } else if (event === 'sources') {
          patchLast((m) => ({ ...m, sources: data.sources, search_query: data.search_query }))
        } else if (event === 'token') {
          patchLast((m) => ({ ...m, content: m.content + data.text }))
        } else if (event === 'done') {
          patchLast((m) => ({ ...m, ...data, id: data.message_id, streaming: false }))
        } else if (event === 'error') {
          patchLast((m) => ({ ...m, error: data.message, streaming: false }))
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError((err as Error).message)
        patchLast((m) => ({ ...m, streaming: false, error: m.error ?? 'Request failed' }))
      }
    } finally {
      setBusy(false)
      void loadConversations()
    }
  }

  const ready = documents.filter((d) => d.status === 'ready')

  return (
    <div className="grid gap-6 md:grid-cols-[14rem_1fr]">
      <aside className="space-y-2">
        <button
          type="button"
          onClick={newConversation}
          className="w-full rounded-lg border border-shiori-500 px-3 py-2 text-sm font-medium text-shiori-700 hover:bg-white"
        >
          + New question
        </button>
        <ul className="max-h-[70vh] space-y-1 overflow-y-auto">
          {conversations.map((c) => (
            <li key={c.id} className="group flex items-center">
              <button
                type="button"
                onClick={() => void openConversation(c.id)}
                className={`min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-left text-sm ${
                  c.id === activeId ? 'bg-white font-medium text-stone-900 shadow-sm' : 'text-stone-600 hover:bg-white/60'
                }`}
                title={c.title}
              >
                {c.title}
              </button>
              <button
                type="button"
                onClick={() => void removeConversation(c.id)}
                className="ml-1 hidden px-1 text-stone-400 hover:text-red-600 group-hover:block"
                title="Delete conversation"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-[70vh] flex-col">
        <div className="flex-1 space-y-5">
          {messages.length === 0 && (
            <div className="rounded-xl border border-dashed border-stone-300 p-8 text-center">
              <p className="font-medium text-stone-700">Ask a question about your papers</p>
              <p className="mt-1 text-sm text-stone-500">
                Answers cite the exact passages they come from. English or 日本語.
              </p>
              <div className="mt-4 flex flex-col items-center gap-1 text-sm">
                {EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => void send(ex)}
                    className="text-shiori-700 hover:underline"
                  >
                    {ex}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.key} className="flex justify-end">
                <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-shiori-500 px-4 py-2 text-white">
                  {m.content}
                </p>
              </div>
            ) : (
              <AssistantMessage key={m.key} message={m} conversationId={activeId} />
            ),
          )}
          <div ref={bottom} />
        </div>

        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

        <form
          onSubmit={(e) => {
            e.preventDefault()
            void send(input)
          }}
          className="sticky bottom-0 mt-4 space-y-2 bg-shiori-50 pb-2 pt-2"
        >
          <div className="flex gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  void send(input)
                }
              }}
              rows={2}
              placeholder={activeId ? 'Ask a follow-up…' : 'Ask about your papers, in English or 日本語'}
              className="min-w-0 flex-1 resize-none rounded-lg border border-stone-300 bg-white px-4 py-2 shadow-sm outline-none focus:border-shiori-500"
            />
            {busy ? (
              <button
                type="button"
                onClick={() => abort.current?.abort()}
                className="rounded-lg border border-stone-300 bg-white px-4 font-medium text-stone-700"
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="rounded-lg bg-shiori-500 px-5 font-medium text-white hover:bg-shiori-700 disabled:opacity-50"
              >
                Ask
              </button>
            )}
          </div>
          <label className="flex items-center gap-2 text-xs text-stone-500">
            Answer from
            <select
              value={documentId}
              onChange={(e) => setDocumentId(e.target.value)}
              className="max-w-64 truncate rounded border border-stone-300 bg-white px-1.5 py-0.5"
            >
              <option value="">all papers</option>
              {ready.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.title}
                </option>
              ))}
            </select>
            <span className="ml-auto">Enter to send · Shift+Enter for a new line</span>
          </label>
        </form>
      </section>
    </div>
  )
}

function AssistantMessage({
  message,
  conversationId,
}: {
  message: UiMessage
  conversationId: string | null
}) {
  const [focused, setFocused] = useState<number | null>(null)
  const sources = message.sources ?? []
  const cited = new Set(message.cited ?? [])
  // While streaming, show every source; afterwards, the cited ones first.
  const shown = message.streaming
    ? sources
    : [...sources].sort((a, b) => Number(cited.has(b.n)) - Number(cited.has(a.n)))

  return (
    <article className="rounded-2xl rounded-bl-sm border border-stone-200 bg-white p-4 shadow-sm">
      {message.plan && <PlanLine plan={message.plan} />}
      {message.content ? (
        <div className="leading-relaxed text-stone-800">
          <CitedMarkdown text={message.content} sources={sources} onCite={setFocused} />
          {message.streaming && <span className="animate-pulse">▍</span>}
        </div>
      ) : message.streaming ? (
        <p className="text-sm text-stone-500">
          {sources.length ? 'Writing the answer…' : 'Searching your papers…'}
        </p>
      ) : null}

      {message.error && (
        <p className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">{message.error}</p>
      )}

      {shown.length > 0 && (
        <details className="mt-3" open={focused !== null}>
          <summary className="cursor-pointer text-xs font-medium text-stone-500">
            {message.streaming ? `${sources.length} sources` : `Sources (${cited.size} cited of ${sources.length})`}
          </summary>
          <ol className="mt-2 space-y-2">
            {shown.map((s) => (
              <SourceCard
                key={s.n}
                source={s}
                cited={cited.has(s.n)}
                focused={focused === s.n}
              />
            ))}
          </ol>
        </details>
      )}

      {!message.streaming && !message.error && message.id && conversationId && (
        <FeedbackButtons
          conversationId={conversationId}
          messageId={message.id}
          initial={message.feedback ?? null}
        />
      )}

      {!message.streaming && message.timings_ms && (
        <p className="mt-3 text-[11px] text-stone-400">
          {[
            message.model,
            message.input_tokens != null &&
              `${message.input_tokens.toLocaleString()} in / ${message.output_tokens?.toLocaleString()} out tokens`,
            message.cost_usd != null && `$${message.cost_usd.toFixed(4)}`,
            `${(message.timings_ms.total / 1000).toFixed(1)} s`,
            message.search_query && message.search_query !== message.content && `searched: “${message.search_query}”`,
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      )}
    </article>
  )
}

/** Renders the answer's markdown (bold, lists, tables) with [n] citations as buttons. */
function CitedMarkdown({
  text,
  sources,
  onCite,
}: {
  text: string
  sources: Source[]
  onCite: (n: number) => void
}) {
  // Turn [1], [1, 2] and ［1］ into one markdown link per source: [1](#cite-1)[2](#cite-2)
  const linked = text.replace(CITATION, (_, group: string) =>
    group
      .split(/[\s,，、]+/)
      .map((n) => `[${n}](#cite-${n})`)
      .join(''),
  )
  const components: Components = {
    a: ({ href, children }) => {
      const n = href?.startsWith('#cite-') ? Number(href.slice(6)) : null
      if (n === null) {
        return (
          <a href={href} target="_blank" rel="noreferrer" className="text-shiori-700 underline">
            {children}
          </a>
        )
      }
      const source = sources.find((s) => s.n === n)
      return (
        <button
          type="button"
          onClick={() => onCite(n)}
          title={source ? `${source.document_title}, p. ${source.page_start}` : `Source ${n}`}
          className="mx-0.5 rounded bg-shiori-50 px-1 align-super text-[10px] font-semibold text-shiori-700 hover:bg-shiori-500 hover:text-white"
        >
          {n}
        </button>
      )
    },
    p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
    ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5">{children}</ol>,
    strong: ({ children }) => <strong className="font-semibold text-stone-900">{children}</strong>,
    h1: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
    h2: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
    h3: ({ children }) => <p className="mb-1 font-semibold">{children}</p>,
    table: ({ children }) => (
      <table className="mb-2 w-full border-collapse text-sm [&_td]:border [&_td]:border-stone-200 [&_td]:p-1.5 [&_th]:border [&_th]:border-stone-200 [&_th]:bg-stone-50 [&_th]:p-1.5">
        {children}
      </table>
    ),
    code: ({ children }) => <code className="rounded bg-stone-100 px-1 text-[0.9em]">{children}</code>,
  }
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkCjkFriendly]} components={components}>
      {linked}
    </ReactMarkdown>
  )
}

function SourceCard({ source, cited, focused }: { source: Source; cited: boolean; focused: boolean }) {
  const ref = useRef<HTMLLIElement>(null)
  const openPdf = useOpenPdf()
  const [expanded, setExpanded] = useState(false)
  const open = expanded || focused // clicking a citation opens its passage

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [focused])

  const pages =
    source.page_start === source.page_end
      ? `p. ${source.page_start}`
      : `pp. ${source.page_start}–${source.page_end}`
  return (
    <li
      ref={ref}
      className={`rounded-lg border p-3 text-sm transition ${
        focused ? 'border-shiori-500 ring-2 ring-shiori-500/20' : 'border-stone-200'
      } ${cited ? 'bg-white' : 'bg-stone-50 opacity-75'}`}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 text-xs">
        <span className="font-mono font-semibold text-shiori-700">[{source.n}]</span>
        <span className="font-medium text-stone-800">{source.document_title}</span>
        <span className="text-stone-500">{source.section ?? 'No section'}</span>
        <button
          type="button"
          onClick={() =>
            openPdf({
              documentId: source.document_id,
              title: source.document_title,
              page: source.page_start,
              pageEnd: source.page_end,
              highlight: source.text,
            })
          }
          className="text-shiori-700 hover:underline"
          title="Open the PDF with this passage highlighted"
        >
          {pages} ↗
        </button>
        {!cited && <span className="text-stone-400">not cited</span>}
      </div>
      <p className={`mt-1 whitespace-pre-line text-stone-700 ${open ? '' : 'line-clamp-2'}`}>{source.text}</p>
      <button type="button" onClick={() => setExpanded(!open)} className="mt-0.5 text-xs text-stone-500 hover:underline">
        {open ? 'Show less' : 'Show passage'}
      </button>
    </li>
  )
}

/** 👍 / 👎 on an answer. A 👎 asks what was wrong; ratings show up in Insights. */
function FeedbackButtons({
  conversationId,
  messageId,
  initial,
}: {
  conversationId: string
  messageId: string
  initial: 1 | -1 | null
}) {
  const [value, setValue] = useState<1 | -1 | null>(initial)
  const [asking, setAsking] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function save(next: 1 | -1 | 0, text?: string) {
    setError(null)
    try {
      await api.setFeedback(conversationId, messageId, next, text)
      setValue(next === 0 ? null : next)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const button = (active: boolean) =>
    `rounded px-1.5 py-0.5 text-sm ${active ? 'bg-shiori-50 ring-1 ring-shiori-500' : 'opacity-60 hover:opacity-100'}`
  return (
    <div className="mt-3 text-xs text-stone-500">
      <div className="flex items-center gap-1">
        <span className="mr-1">Was this helpful?</span>
        <button type="button" aria-label="Helpful" aria-pressed={value === 1} className={button(value === 1)}
          onClick={() => { setAsking(false); void save(value === 1 ? 0 : 1) }}>
          👍
        </button>
        <button type="button" aria-label="Not helpful" aria-pressed={value === -1} className={button(value === -1)}
          onClick={() => (value === -1 ? void save(0) : setAsking(true))}>
          👎
        </button>
        {value !== null && !asking && <span className="ml-1">Thanks!</span>}
      </div>
      {asking && (
        <form
          className="mt-2 flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setAsking(false)
            void save(-1, note)
          }}
        >
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="What was wrong? (optional, e.g. missed an example, wrong page)"
            className="min-w-0 flex-1 rounded border border-stone-300 px-2 py-1 text-stone-700 outline-none focus:border-shiori-500"
            autoFocus
          />
          <button className="rounded bg-stone-800 px-3 py-1 text-white">Send</button>
        </form>
      )}
      {error && <p className="mt-1 text-red-600">{error}</p>}
    </div>
  )
}

const PLAN_LABELS: Record<AgentPlan['question_type'], string> = {
  details: 'Paper details',
  lookup: 'Search',
  find_all: 'Read the whole paper',
  compare: 'Compare papers',
  summary: 'Summary',
  relations: 'Knowledge graph',
}

/** What the agent did before answering, e.g. "Read the whole paper · Read all 4 passages". */
function PlanLine({ plan }: { plan: AgentPlan }) {
  return (
    <p className="mb-2 flex flex-wrap items-center gap-x-1.5 text-xs text-stone-500" title={`Understood as: ${plan.question}`}>
      <span className="rounded bg-stone-100 px-1.5 py-0.5 font-medium text-stone-600">
        {PLAN_LABELS[plan.question_type]}
      </span>
      {plan.steps.join(' · ')}
    </p>
  )
}

