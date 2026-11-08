import { useState, type FormEvent } from 'react'

import { api } from '../api'
import { useAuth } from './useAuth'

export function UserMenu() {
  const { user, logout } = useAuth()
  const [open, setOpen] = useState(false)
  const [changing, setChanging] = useState(false)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-md px-2 py-1 text-sm text-stone-700 hover:bg-stone-100"
      >
        <span className="grid h-7 w-7 place-items-center rounded-full bg-shiori-500 text-xs font-semibold text-white">
          {user.name.slice(0, 1).toUpperCase()}
        </span>
        <span className="hidden sm:inline">{user.name}</span>
        {user.role === 'admin' && (
          <span className="rounded bg-stone-100 px-1.5 text-[10px] uppercase text-stone-500">admin</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 z-20 mt-1 w-56 rounded-lg border border-stone-200 bg-white py-1 text-sm shadow-lg">
          <p className="truncate px-3 py-1.5 text-xs text-stone-500">{user.email}</p>
          <button type="button" className="block w-full px-3 py-1.5 text-left hover:bg-stone-50"
            onClick={() => { setChanging(true); setOpen(false) }}>
            Change password
          </button>
          <button type="button" className="block w-full px-3 py-1.5 text-left text-red-600 hover:bg-stone-50"
            onClick={() => void logout()}>
            Log out
          </button>
        </div>
      )}
      {changing && <ChangePasswordDialog onClose={() => setChanging(false)} />}
    </div>
  )
}

function ChangePasswordDialog({ onClose }: { onClose: () => void }) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      await api.changePassword(current, next)
      setDone(true)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const field = 'w-full rounded-lg border border-stone-300 px-3 py-2 outline-none focus:border-shiori-500'
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-stone-900/40 px-4" role="dialog" aria-modal="true">
      <form onSubmit={submit} className="w-full max-w-sm space-y-3 rounded-xl bg-white p-6 shadow-xl">
        <h2 className="text-lg font-semibold">Change password</h2>
        {done ? (
          <p className="text-sm text-green-700">Password changed. Your other devices are logged out.</p>
        ) : (
          <>
            <input className={field} type="password" placeholder="Current password" autoComplete="current-password"
              value={current} onChange={(e) => setCurrent(e.target.value)} required autoFocus />
            <input className={field} type="password" placeholder="New password (8+ characters)" minLength={8}
              autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
            {error && <p className="text-sm text-red-600">{error}</p>}
          </>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-stone-600 hover:bg-stone-100">
            {done ? 'Close' : 'Cancel'}
          </button>
          {!done && (
            <button className="rounded-lg bg-shiori-500 px-4 py-2 text-sm font-medium text-white hover:bg-shiori-700">
              Change password
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
