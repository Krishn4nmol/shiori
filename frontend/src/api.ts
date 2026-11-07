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
  fileUrl: (id: string) => `/api/documents/${id}/file`,
}
