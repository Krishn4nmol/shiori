import { createContext, useContext } from 'react'

import type { User } from '../api'

export type Auth = {
  user: User
  logout: () => Promise<void>
}

export const AuthContext = createContext<Auth | null>(null)

/** The logged-in user. Only usable inside <AuthGate>, which guarantees someone is logged in. */
export function useAuth(): Auth {
  const auth = useContext(AuthContext)
  if (!auth) throw new Error('useAuth must be used inside <AuthGate>')
  return auth
}

export function canModifyDocument(user: User, uploadedById: string | null): boolean {
  return user.role === 'admin' || uploadedById === user.id
}
