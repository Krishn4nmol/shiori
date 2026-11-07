import { useEffect, useState } from 'react'

import { api, type Chunk } from '../api'

const PAGE_SIZE = 20

export function ChunkViewer({ documentId }: { documentId: string }) {
  const [chunks, setChunks] = useState<Chunk[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  async function load(offset: number) {
    setLoading(true)
    try {
      const page = await api.listChunks(documentId, offset, PAGE_SIZE)
      setChunks((prev) => (offset === 0 ? page.items : [...prev, ...page.items]))
      setTotal(page.total)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load(0)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [documentId])

  if (error) return <p className="text-sm text-red-600">{error}</p>

  return (
    <div className="space-y-2">
      {chunks.map((c) => (
        <article key={c.id} className="rounded-lg border border-stone-200 bg-stone-50 p-3">
          <header className="mb-1 flex flex-wrap gap-x-3 text-xs text-stone-500">
            <span className="font-mono">#{c.chunk_index}</span>
            <span>{c.page_start === c.page_end ? `p. ${c.page_start}` : `pp. ${c.page_start}–${c.page_end}`}</span>
            <span>{c.token_count} tokens</span>
            <span className="uppercase">{c.language}</span>
            {c.section && <span className="font-medium text-stone-700">{c.section}</span>}
          </header>
          <p className="whitespace-pre-line text-sm leading-relaxed text-stone-800">{c.text}</p>
        </article>
      ))}
      {chunks.length < total && (
        <button
          type="button"
          onClick={() => void load(chunks.length)}
          disabled={loading}
          className="text-sm font-medium text-shiori-700 hover:underline disabled:opacity-50"
        >
          {loading ? 'Loading…' : `Show more (${total - chunks.length} left)`}
        </button>
      )}
      {loading && chunks.length === 0 && <p className="text-sm text-stone-500">Loading chunks…</p>}
    </div>
  )
}
