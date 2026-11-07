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
  created_at: string
  updated_at: string
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
  created_at: string
}

export type Conversation = { id: string; title: string; created_at: string; updated_at: string }

export type ChatEvent =
  | { event: 'conversation'; data: { id: string; title: string } }
  | { event: 'sources'; data: { sources: Source[]; search_query: string } }
  | { event: 'token'; data: { text: string } }
  | {
      event: 'done'
      data: Omit<ChatMessage, 'id' | 'role' | 'created_at' | 'error'> & { message_id: string }
    }
  | { event: 'error'; data: { message: string } }

export type Health = {
  status: string
  version: string
  database: 'ok' | 'unavailable'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail ?? `Request failed (HTTP ${res.status})`)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export const api = {
  health: () => request<Health>('/health'),
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
  if (!res.ok || !res.body) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `Request failed (HTTP ${res.status})`)
  }
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
