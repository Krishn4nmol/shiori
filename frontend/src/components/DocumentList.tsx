import { useState } from 'react'

import { api, type DocumentStatus, type ShioriDocument } from '../api'
import { ChunkViewer } from './ChunkViewer'

const STATUS_STYLE: Record<DocumentStatus, string> = {
  pending: 'bg-stone-100 text-stone-600',
  processing: 'bg-amber-100 text-amber-800',
  ready: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-700',
}

const LANGUAGE_LABEL = { en: 'English', ja: '日本語', mixed: 'EN + 日本語' } as const

function formatSize(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

type Props = { documents: ShioriDocument[]; onChange: () => void }

export function DocumentList({ documents, onChange }: Props) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>) {
    setActionError(null)
    try {
      await action()
      onChange()
    } catch (err) {
      setActionError((err as Error).message)
    }
  }

  if (documents.length === 0) {
    return <p className="py-8 text-center text-stone-500">No papers yet. Upload one to start.</p>
  }

  return (
    <div className="space-y-3">
      {actionError && <p className="text-sm text-red-600">{actionError}</p>}
      {documents.map((doc) => {
        const busy = doc.status === 'pending' || doc.status === 'processing'
        return (
          <article key={doc.id} className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h3 className="truncate font-semibold text-stone-900" title={doc.title}>
                  {doc.title}
                </h3>
                <p className="mt-0.5 text-sm text-stone-500">
                  {[
                    doc.authors,
                    doc.page_count && `${doc.page_count} pages`,
                    doc.status === 'ready' && `${doc.chunk_count} chunks`,
                    doc.language && LANGUAGE_LABEL[doc.language],
                    formatSize(doc.size_bytes),
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </p>
              </div>
              <span
                className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLE[doc.status]}`}
              >
                {doc.status}
              </span>
            </div>

            {busy && (
              <div className="mt-3">
                <div className="h-1.5 overflow-hidden rounded-full bg-stone-100">
                  <div
                    className="h-full rounded-full bg-shiori-500 transition-all duration-500"
                    style={{ width: `${Math.max(doc.progress, 3)}%` }}
                  />
                </div>
                <p className="mt-1 text-xs text-stone-500">
                  {doc.stage ?? 'Waiting for the worker…'} · {doc.progress}%
                </p>
              </div>
            )}

            {doc.status === 'failed' && doc.error && (
              <p className="mt-2 rounded-md bg-red-50 p-2 text-sm text-red-700">{doc.error}</p>
            )}

            <div className="mt-3 flex flex-wrap gap-4 text-sm">
              {doc.status === 'ready' && (
                <button
                  type="button"
                  onClick={() => setOpenId(openId === doc.id ? null : doc.id)}
                  className="font-medium text-shiori-700 hover:underline"
                >
                  {openId === doc.id ? 'Hide chunks' : 'View chunks'}
                </button>
              )}
              <a
                href={api.fileUrl(doc.id)}
                target="_blank"
                rel="noreferrer"
                className="text-stone-600 hover:underline"
              >
                Open PDF
              </a>
              {!busy && (
                <button
                  type="button"
                  onClick={() => void run(() => api.reingestDocument(doc.id))}
                  className="text-stone-600 hover:underline"
                >
                  Re-process
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  if (confirm(`Delete "${doc.title}" and its chunks? This cannot be undone.`))
                    void run(() => api.deleteDocument(doc.id))
                }}
                className="text-red-600 hover:underline"
              >
                Delete
              </button>
            </div>

            {openId === doc.id && (
              <div className="mt-4 max-h-[32rem] overflow-y-auto">
                <ChunkViewer documentId={doc.id} />
              </div>
            )}
          </article>
        )
      })}
    </div>
  )
}
