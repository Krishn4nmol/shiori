import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from 'react'

import { api, HttpError, LOGGED_OUT_EVENT, type User } from '../api'
import { AuthContext } from './useAuth'

type State =
  | { kind: 'loading' }
  | { kind: 'unreachable'; message: string }
  | { kind: 'setup' }
  | { kind: 'login'; notice?: string }
  | { kind: 'ready'; user: User }

/** Shows the first-run setup or the login page until someone is logged in. */
export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  const check = useCallback(async () => {
    try {
      const { setup_required } = await api.authStatus()
      if (setup_required) return setState({ kind: 'setup' })
      setState({ kind: 'ready', user: await api.me() })
    } catch (err) {
      if (err instanceof HttpError && err.status === 401) setState({ kind: 'login' })
      else setState({ kind: 'unreachable', message: (err as Error).message })
    }
  }, [])

  useEffect(() => {
    void check()
    const onLoggedOut = () => setState({ kind: 'login', notice: 'Your session ended. Please log in again.' })
    window.addEventListener(LOGGED_OUT_EVENT, onLoggedOut)
    return () => window.removeEventListener(LOGGED_OUT_EVENT, onLoggedOut)
  }, [check])

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined)
    setState({ kind: 'login' })
  }, [])

  if (state.kind === 'ready') {
    return <AuthContext.Provider value={{ user: state.user, logout }}>{children}</AuthContext.Provider>
  }
  return (
    <div className="grid min-h-screen place-items-center bg-shiori-50 px-4 text-stone-800">
      <div className="w-full max-w-sm">
        <p className="mb-6 text-center text-2xl font-bold">栞 Shiori</p>
        {state.kind === 'loading' && <p className="text-center text-stone-500">Loading…</p>}
        {state.kind === 'unreachable' && (
          <p className="text-center text-red-600">
            Can't reach the Shiori API ({state.message}). Is the backend running?
          </p>
        )}
        {state.kind === 'setup' && <SetupForm onDone={(user) => setState({ kind: 'ready', user })} />}
        {state.kind === 'login' && (
          <LoginForm notice={state.notice} onDone={(user) => setState({ kind: 'ready', user })} />
        )}
      </div>
    </div>
  )
}

const input =
  'w-full rounded-lg border border-stone-300 bg-white px-3 py-2 outline-none focus:border-shiori-500'
const primary =
  'w-full rounded-lg bg-shiori-500 py-2 font-medium text-white hover:bg-shiori-700 disabled:opacity-50'

function LoginForm({ notice, onDone }: { notice?: string; onDone: (user: User) => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await api.login(email, password))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold">Log in</h1>
      {notice && <p className="rounded-md bg-amber-50 p-2 text-sm text-amber-800">{notice}</p>}
      <input className={input} type="email" placeholder="Email" autoComplete="username" value={email}
        onChange={(e) => setEmail(e.target.value)} required autoFocus />
      <input className={input} type="password" placeholder="Password" autoComplete="current-password"
        value={password} onChange={(e) => setPassword(e.target.value)} required />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button className={primary} disabled={busy}>{busy ? 'Logging in…' : 'Log in'}</button>
      <p className="text-center text-xs text-stone-500">No account? Ask your lab's Shiori admin.</p>
    </form>
  )
}

function SetupForm({ onDone }: { onDone: (user: User) => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (password !== confirm) return setError('The passwords do not match.')
    setBusy(true)
    setError(null)
    try {
      onDone(await api.setup({ name, email, password }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-stone-200 bg-white p-6 shadow-sm">
      <h1 className="text-lg font-semibold">Welcome! Create the admin account</h1>
      <p className="text-sm text-stone-500">
        This is the first run. The admin can add lab members later. Papers and questions you
        already have will belong to this account.
      </p>
      <input className={input} placeholder="Your name" value={name} onChange={(e) => setName(e.target.value)} required autoFocus />
      <input className={input} type="email" placeholder="Email" autoComplete="username" value={email}
        onChange={(e) => setEmail(e.target.value)} required />
      <input className={input} type="password" placeholder="Password (at least 8 characters)" minLength={8}
        autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
      <input className={input} type="password" placeholder="Repeat password" autoComplete="new-password"
        value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
      {error && <p className="text-sm text-red-600">{error}</p>}
      <button className={primary} disabled={busy}>{busy ? 'Creating…' : 'Create admin account'}</button>
    </form>
  )
}
