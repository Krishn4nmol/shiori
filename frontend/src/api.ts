// Typed wrappers around the Shiori API. Types mirror backend/app/api/*.py.

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed'

export type ShioriDocument = {
  id: string
  title: string
  filename: string
  size_bytes: number
  authors: string | null
  language: 'en' | 'ja' | 'mixed' | null
  page_count: number | null
  chunk_count: number
  status: DocumentStatus
  progress: number
  stage: string | null
  error: string | null
  uploaded_by_id: string | null
  graph_status: 'none' | 'building' | 'ready' | 'failed'
  graph_entities: number
  graph_relations: number
  graph_error: string | null
  created_at: string
  updated_at: string
}

export type User = {
  id: string
  email: string
  name: string
  role: 'admin' | 'member'
  is_active: boolean
}

/** Fired when the server says the session is gone; the app then shows the login page. */
export const LOGGED_OUT_EVENT = 'shiori:logged-out'

export class HttpError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export type Chunk = {
  id: string
  chunk_index: number
  text: string
  section: string | null
  page_start: number
  page_end: number
  language: string
  token_count: number
}

export type SearchMode = 'hybrid' | 'vector' | 'keyword'

export type SearchHit = {
  chunk_id: string
  document_id: string
  document_title: string
  text: string
  section: string | null
  page_start: number
  page_end: number
  language: string
  vector_rank: number | null
  vector_score: number | null
  keyword_rank: number | null
  keyword_score: number | null
  rrf_score: number
  rerank_score: number | null
}

export type SearchResponse = {
  query: string
  mode: SearchMode
  reranked: boolean
  candidates: number
  min_score: number | null
  filtered_out: number
  best_rerank_score: number | null
  timings_ms: Record<string, number>
  hits: SearchHit[]
}

export type SearchRequest = {
  query: string
  k?: number
  mode?: SearchMode
  rerank?: boolean
  document_ids?: string[]
  language?: 'en' | 'ja'
  min_score?: number
}

export type Source = {
  n: number
  chunk_id: string
  document_id: string
  document_title: string
  section: string | null
  page_start: number
  page_end: number
  language: string
  text: string
  rerank_score: number | null
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources: Source[] | null
  cited: number[] | null
  search_query: string | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
  timings_ms: Record<string, number> | null
  error: string | null
  feedback: 1 | -1 | null
  feedback_note: string | null
  created_at: string
}

/** How the agent decided to answer (see backend/app/agent). */
export type AgentPlan = {
  question_type: 'details' | 'lookup' | 'find_all' | 'compare' | 'summary' | 'relations'
  question: string
  queries: string[]
  papers: { id: string; title: string }[]
  entities: string[]
  fallback: boolean
  steps: string[]
}

export type Conversation = { id: string; title: string; created_at: string; updated_at: string }

export type ChatEvent =
  | { event: 'conversation'; data: { id: string; title: string } }
  | { event: 'plan'; data: AgentPlan }
  | { event: 'sources'; data: { sources: Source[]; search_query: string } }
  | { event: 'token'; data: { text: string } }
  | {
      event: 'done'
      data: Omit<ChatMessage, 'id' | 'role' | 'created_at' | 'error' | 'feedback' | 'feedback_note'> & {
        message_id: string
      }
    }
  | { event: 'error'; data: { message: string } }

export type Percentiles = { p50: number | null; p95: number | null }

export type InsightsSummary = {
  days: number
  questions: number
  searches: number
  answered_rate: number | null
  error_rate: number | null
  latency_ms: Percentiles
  first_token_ms: Percentiles
  stage_avg_ms: Record<string, number>
  search_stage_avg_ms: Record<string, number>
  cost_usd: number
  input_tokens: number
  output_tokens: number
  feedback_up: number
  feedback_down: number
  per_day: { day: string; questions: number; unanswered: number }[]
  unanswered_questions: string[]
}

export type TraceRow = {
  id: string
  kind: 'chat' | 'search'
  created_at: string
  user_name: string | null
  query: string
  answered: boolean
  error: string | null
  n_candidates: number
  filtered_out: number
  best_rerank_score: number | null
  latency_ms: number
  cost_usd: number
  feedback: 1 | -1 | null
}

export type TraceCandidate = {
  chunk_id: string
  document_id: string
  document_title: string
  section: string | null
  page_start: number
  page_end: number
  language: string
  preview: string
  vector_rank: number | null
  vector_score: number | null
  keyword_rank: number | null
  rerank_score: number | null
  kept: boolean
  cited: boolean
}

export type TraceDetail = TraceRow & {
  search_query: string | null
  conversation_id: string | null
  candidates: TraceCandidate[]
  timings_ms: Record<string, number>
  model: string | null
  input_tokens: number
  output_tokens: number
  prompt_version: string | null
  prompt_is_current: boolean
  plan: AgentPlan | null
  system_prompt: string | null
  answer: string | null
  feedback_note: string | null
}

export type TraceFilter = 'unanswered' | 'errors' | 'negative'

export type EvalQuestion = {
  id: string
  question: string
  language: string
  expected_answer: string | null
  document_id: string | null
  evidence: string | null
  source: 'manual' | 'generated' | 'trace'
  created_at: string
}

export type EvalQuestionIn = {
  question: string
  expected_answer?: string | null
  document_id?: string | null
  evidence?: string | null
}

export type SweepPoint = {
  cutoff: number
  coverage: number | null
  rejection: number | null
  balanced: number | null
}

export type EvalMetrics = {
  questions: number
  answerable: number
  unanswerable: number
  'hit@1'?: number
  'hit@3'?: number
  'hit@5'?: number
  mrr?: number
  coverage?: number
  rejection?: number
  latency_p50_ms?: number
  cost_usd: number
  errors: number
  sweep: { points: SweepPoint[]; best: SweepPoint | null }
  judged?: number
  correctness?: number
  faithfulness?: number
  cited_relevant?: number
  said_not_found?: number
  language_match?: number
}

export type EvalRun = {
  id: string
  name: string
  kind: 'retrieval' | 'answer'
  config: {
    mode: SearchMode
    rerank: boolean
    min_rerank_score: number
    llm_model: string | null
    prompt_version: string | null
    [key: string]: unknown
  }
  status: 'running' | 'done' | 'failed'
  progress: number
  total: number
  metrics: EvalMetrics | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export type EvalResultRow = {
  question_id: string
  question: string
  expected_answer: string | null
  evidence: string | null
  relevant_rank: number | null
  kept_relevant: boolean
  answered: boolean
  candidates: {
    chunk_id: string
    document_title: string
    page_start: number
    preview: string
    vector_rank: number | null
    keyword_rank: number | null
    rerank_score: number | null
    relevant: boolean
    kept: boolean
  }[]
  answer: string | null
  judge: {
    correctness?: 'correct' | 'partial' | 'wrong'
    faithful?: boolean | null
    declined?: boolean
    reason?: string
    cited_relevant?: boolean
    said_not_found?: boolean
  } | null
  latency_ms: number
  cost_usd: number
  error: string | null
}

export type EvalRunIn = {
  name: string
  kind: 'retrieval' | 'answer'
  mode: SearchMode
  rerank: boolean
  min_rerank_score?: number | null
  agent?: boolean
}

export type GraphNode = {
  key: string
  name: string
  type: string
  aliases: string[]
  degree: number
  papers: string[]
}

export type GraphEdge = {
  source: string
  target: string
  relation: string
  chunk_id: string
  paper_id: string
  evidence: string
}

export type GraphFact = {
  source: string
  relation: string
  target: string
  chunk_id: string
  paper_id: string
  evidence: string
}

export type GraphEntity = {
  key: string
  name: string
  type: string
  aliases: string[]
  mentions: { chunk_id: string; page_start: number; paper_id: string; paper_title: string }[]
  facts: GraphFact[]
}

export type Health = {
  status: string
  version: string
  database: 'ok' | 'unavailable'
}

async function errorFrom(res: Response): Promise<HttpError> {
  const body = await res.json().catch(() => null)
  const detail = Array.isArray(body?.detail)
    ? body.detail.map((d: { msg: string }) => d.msg.replace(/^Value error, /, '')).join('; ')
    : body?.detail
  if (res.status === 401 && !res.url.includes('/api/auth/')) {
    window.dispatchEvent(new Event(LOGGED_OUT_EVENT))
  }
  return new HttpError(detail ?? `Request failed (HTTP ${res.status})`, res.status)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) throw await errorFrom(res)
  return (res.status === 204 ? undefined : await res.json()) as T
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  health: () => request<Health>('/health'),
  authStatus: () => request<{ setup_required: boolean }>('/auth/status'),
  setup: (body: { email: string; name: string; password: string }) =>
    request<User>('/auth/setup', json('POST', body)),
  login: (email: string, password: string) =>
    request<User>('/auth/login', json('POST', { email, password })),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),
  changePassword: (current_password: string, new_password: string) =>
    request<void>('/auth/password', json('POST', { current_password, new_password })),
  listUsers: () => request<User[]>('/users'),
  createUser: (body: { email: string; name: string; password: string; role: User['role'] }) =>
    request<User>('/users', json('POST', body)),
  updateUser: (
    id: string,
    body: Partial<{ name: string; role: User['role']; is_active: boolean; password: string }>,
  ) => request<User>(`/users/${id}`, json('PATCH', body)),
  listDocuments: () => request<ShioriDocument[]>('/documents'),
  uploadDocument: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ShioriDocument>('/documents', { method: 'POST', body: form })
  },
  reingestDocument: (id: string) =>
    request<ShioriDocument>(`/documents/${id}/reingest`, { method: 'POST' }),
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: 'DELETE' }),
  listChunks: (id: string, offset = 0, limit = 50) =>
    request<{ total: number; items: Chunk[] }>(
      `/documents/${id}/chunks?offset=${offset}&limit=${limit}`,
    ),
  fileUrl: (id: string, page?: number) =>
    `/api/documents/${id}/file${page ? `#page=${page}` : ''}`,
  listConversations: () => request<Conversation[]>('/conversations'),
  getConversation: (id: string) =>
    request<Conversation & { messages: ChatMessage[] }>(`/conversations/${id}`),
  deleteConversation: (id: string) =>
    request<void>(`/conversations/${id}`, { method: 'DELETE' }),
  setFeedback: (conversationId: string, messageId: string, value: 1 | -1 | 0, note?: string) =>
    request<void>(
      `/conversations/${conversationId}/messages/${messageId}/feedback`,
      json('PUT', { value, note }),
    ),
  insightsSummary: (days: number) => request<InsightsSummary>(`/insights/summary?days=${days}`),
  listTraces: (params: { kind?: 'chat' | 'search'; only?: TraceFilter; offset?: number }) => {
    const q = new URLSearchParams({ limit: '25', offset: String(params.offset ?? 0) })
    if (params.kind) q.set('kind', params.kind)
    if (params.only) q.set('only', params.only)
    return request<{ total: number; items: TraceRow[] }>(`/insights/traces?${q}`)
  },
  getTrace: (id: string) => request<TraceDetail>(`/insights/traces/${id}`),
  evalQuestions: () => request<EvalQuestion[]>('/eval/questions'),
  addEvalQuestion: (body: EvalQuestionIn) =>
    request<EvalQuestion>('/eval/questions', json('POST', body)),
  deleteEvalQuestion: (id: string) => request<void>(`/eval/questions/${id}`, { method: 'DELETE' }),
  generateEvalQuestions: (count: number, unanswerable: number) =>
    request<{ created: EvalQuestion[]; skipped: string[] }>(
      '/eval/questions/generate',
      json('POST', { count, unanswerable }),
    ),
  evalRuns: () => request<EvalRun[]>('/eval/runs'),
  startEvalRun: (body: EvalRunIn) => request<EvalRun>('/eval/runs', json('POST', body)),
  evalRun: (id: string) => request<EvalRun & { results: EvalResultRow[] }>(`/eval/runs/${id}`),
  deleteEvalRun: (id: string) => request<void>(`/eval/runs/${id}`, { method: 'DELETE' }),
  buildGraph: (documentId: string) =>
    request<ShioriDocument>(`/graph/papers/${documentId}/build`, { method: 'POST' }),
  graphOverview: (documentId?: string) =>
    request<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
      `/graph/overview${documentId ? `?document_id=${documentId}` : ''}`,
    ),
  graphEntity: (key: string) => request<GraphEntity>(`/graph/entities/${encodeURIComponent(key)}`),
  search: (body: SearchRequest) =>
    request<SearchResponse>('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}

/** Ask a question and receive the answer as it is written (Server-Sent Events over POST). */
export async function* streamChat(
  body: { content: string; conversation_id?: string; document_ids?: string[] },
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw await errorFrom(res)
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      let event = ''
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (event && data) yield { event, data: JSON.parse(data) } as ChatEvent
    }
  }
}
