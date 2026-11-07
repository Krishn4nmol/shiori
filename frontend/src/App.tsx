import { useCallback, useEffect, useState } from 'react'

import { api, type Health, type ShioriDocument } from './api'
import { DocumentList } from './components/DocumentList'
import { UploadZone } from './components/UploadZone'

const ACTIVE_POLL_MS = 1500 // while something is processing
const IDLE_POLL_MS = 15000

function App() {
  const [documents, setDocuments] = useState<ShioriDocument[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)

  const refresh = useCallback(async () => {
    try {
      setDocuments(await api.listDocuments())
      setLoadError(null)
    } catch (err) {
      setLoadError((err as Error).message)
    }
  }, [])

  const active = documents.some((d) => d.status === 'pending' || d.status === 'processing')

  useEffect(() => {
    void refresh()
    const timer = setInterval(() => void refresh(), active ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(timer)
  }, [refresh, active])

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  const dbOk = health?.database === 'ok'

  return (
    <div className="min-h-screen bg-shiori-50 text-stone-800">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between px-6 py-4">
          <div>
            <span className="text-xl font-bold">栞 Shiori</span>
            <span className="ml-3 text-sm text-stone-500">Cited answers from your research papers</span>
          </div>
          <span className="flex items-center gap-2 text-xs text-stone-500" title="API and database status">
            <span className={`h-2 w-2 rounded-full ${dbOk ? 'bg-green-500' : 'bg-amber-500'}`} />
            {health ? (dbOk ? 'Connected' : 'Database unavailable') : 'API unreachable'}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-4xl space-y-6 px-6 py-8">
        <section>
          <h2 className="mb-3 text-lg font-semibold">Library</h2>
          <UploadZone onUploaded={() => void refresh()} />
        </section>

        <section>
          {loadError ? (
            <p className="text-red-600">Could not load documents: {loadError}</p>
          ) : (
            <DocumentList documents={documents} onChange={() => void refresh()} />
          )}
        </section>
      </main>
    </div>
  )
}

export default App
