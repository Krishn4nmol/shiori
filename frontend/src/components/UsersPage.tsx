import { useCallback, useEffect, useState, type FormEvent } from 'react'

import { api, type User } from '../api'
import { useAuth } from './useAuth'

/** Admin-only: add lab members, change roles, deactivate, reset passwords. */
export function UsersPage() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setUsers(await api.listUsers())
    } catch (err) {
      setError((err as Error).message)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function update(user: User, change: Parameters<typeof api.updateUser>[1], done?: string) {
    setError(null)
    setNotice(null)
    try {
      await api.updateUser(user.id, change)
      if (done) setNotice(done)
      await load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function resetPassword(user: User) {
    const password = prompt(`New temporary password for ${user.name} (at least 8 characters):`)
    if (password) void update(user, { password }, `Password reset. Give ${user.name} the new password; they're logged out everywhere.`)
  }

  return (
    <div className="space-y-6">
      <section>
        <h2 className="mb-3 text-lg font-semibold">Users</h2>
        {error && <p className="mb-2 text-sm text-red-600">{error}</p>}
        {notice && <p className="mb-2 text-sm text-green-700">{notice}</p>}
        <div className="overflow-x-auto rounded-xl border border-stone-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-stone-50 text-left text-xs uppercase tracking-wide text-stone-500">
              <tr>
                <th className="px-4 py-2">Name</th>
                <th className="px-4 py-2">Email</th>
                <th className="px-4 py-2">Role</th>
                <th className="px-4 py-2">Status</th>
                <th className="px-4 py-2" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-stone-100">
                  <td className="px-4 py-2 font-medium">
                    {u.name}
                    {u.id === me.id && <span className="ml-1 text-xs text-stone-400">(you)</span>}
                  </td>
                  <td className="px-4 py-2 text-stone-600">{u.email}</td>
                  <td className="px-4 py-2">
                    <select
                      value={u.role}
                      disabled={u.id === me.id}
                      onChange={(e) => void update(u, { role: e.target.value as User['role'] })}
                      className="rounded border border-stone-300 bg-white px-1.5 py-0.5"
                    >
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td className="px-4 py-2">
                    <span className={u.is_active ? 'text-green-700' : 'text-stone-400'}>
                      {u.is_active ? 'active' : 'deactivated'}
                    </span>
                  </td>
                  <td className="space-x-3 whitespace-nowrap px-4 py-2 text-right">
                    <button type="button" onClick={() => resetPassword(u)} className="text-stone-600 hover:underline">
                      Reset password
                    </button>
                    {u.id !== me.id && (
                      <button
                        type="button"
                        onClick={() => void update(u, { is_active: !u.is_active })}
                        className={u.is_active ? 'text-red-600 hover:underline' : 'text-green-700 hover:underline'}
                      >
                        {u.is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <AddUserForm onAdded={(u) => { setNotice(`${u.name} can now log in as ${u.email}.`); void load() }} />
    </div>
  )
}

function AddUserForm({ onAdded }: { onAdded: (user: User) => void }) {
  const [form, setForm] = useState({ name: '', email: '', password: '', role: 'member' as User['role'] })
  const [error, setError] = useState<string | null>(null)
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) =>
    setForm({ ...form, [key]: e.target.value })

  async function submit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    try {
      onAdded(await api.createUser(form))
      setForm({ name: '', email: '', password: '', role: 'member' })
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const field = 'rounded-lg border border-stone-300 bg-white px-3 py-2 outline-none focus:border-shiori-500'
  return (
    <form onSubmit={submit} className="rounded-xl border border-stone-200 bg-white p-4 shadow-sm">
      <h3 className="mb-3 font-semibold">Add a lab member</h3>
      <div className="grid gap-2 sm:grid-cols-2">
        <input className={field} placeholder="Name" value={form.name} onChange={set('name')} required />
        <input className={field} type="email" placeholder="Email" value={form.email} onChange={set('email')} required />
        <input className={field} type="text" placeholder="Temporary password (8+ characters)" minLength={8}
          value={form.password} onChange={set('password')} required autoComplete="off" />
        <select className={field} value={form.role} onChange={set('role')}>
          <option value="member">Member: upload, search, ask</option>
          <option value="admin">Admin: also manages users</option>
        </select>
      </div>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      <button className="mt-3 rounded-lg bg-shiori-500 px-4 py-2 text-sm font-medium text-white hover:bg-shiori-700">
        Add user
      </button>
      <p className="mt-2 text-xs text-stone-500">
        Share the temporary password privately. They can change it from their menu after logging in.
      </p>
    </form>
  )
}
