import { useEffect, useState } from 'react'

type Health = {
  status: string
  version: string
  database: 'ok' | 'unavailable'
}

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<Health>
      })
      .then(setHealth)
      .catch((err: Error) => setError(err.message))
  }, [])

  return (
    <main className="min-h-screen bg-shiori-50 text-stone-800">
      <div className="mx-auto max-w-2xl px-6 py-24">
        <p className="text-sm font-medium tracking-widest text-shiori-500">栞 · SHIORI</p>
        <h1 className="mt-2 text-4xl font-bold">Cited answers from your research papers</h1>
        <p className="mt-4 text-stone-600">
          Upload papers, ask questions in English or Japanese, and get answers that point to the
          exact page.
        </p>

        <section className="mt-10 rounded-xl border border-stone-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
            System status
          </h2>
          {error && <p className="mt-2 text-red-600">Backend unreachable: {error}</p>}
          {!error && !health && <p className="mt-2 text-stone-500">Checking…</p>}
          {health && (
            <ul className="mt-2 space-y-1">
              <li>
                API: <span className="font-medium text-green-700">{health.status}</span> (v
                {health.version})
              </li>
              <li>
                Database:{' '}
                <span
                  className={
                    health.database === 'ok'
                      ? 'font-medium text-green-700'
                      : 'font-medium text-amber-600'
                  }
                >
                  {health.database}
                </span>
              </li>
            </ul>
          )}
        </section>
      </div>
    </main>
  )
}

export default App
