import { useCallback, useEffect, useState } from 'react'

import { api, type Health, type ShioriDocument } from './api'
import { DocumentList } from './components/DocumentList'
import { SearchPage } from './components/SearchPage'
import { UploadZone } from './components/UploadZone'

const ACTIVE_POLL_MS = 1500 // while something is processing
const IDLE_POLL_MS = 15000

type Tab = 'library' | 'search'

function App() {
  const [documents, setDocuments] = useState<ShioriDocument[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [tab, setTab] = useState<Tab>('library')

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
          <div className="flex items-center gap-6">
            <span className="text-xl font-bold">栞 Shiori</span>
            <nav className="flex gap-1 text-sm">
              {(['library', 'search'] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTab(t)}
                  className={`rounded-md px-3 py-1.5 capitalize ${
                    tab === t ? 'bg-shiori-50 font-medium text-shiori-700' : 'text-stone-600 hover:bg-stone-100'
                  }`}
                >
                  {t}
                </button>
              ))}
            </nav>
          </div>
          <span className="flex items-center gap-2 text-xs text-stone-500" title="API and database status">
            <span className={`h-2 w-2 rounded-full ${dbOk ? 'bg-green-500' : 'bg-amber-500'}`} />
            {health ? (dbOk ? 'Connected' : 'Database unavailable') : 'API unreachable'}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-4xl space-y-6 px-6 py-8">
        {tab === 'search' ? (
          <SearchPage documents={documents} />
        ) : (
          <>
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
          </>
        )}
      </main>
    </div>
  )
}

export default App
