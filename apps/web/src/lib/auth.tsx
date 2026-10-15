import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { api, getToken, setToken } from './api'
import type { CurrentUser, Role } from './types'

interface AuthState {
  user: CurrentUser | null
  loading: boolean
  error: string
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  /** Vrai si le rôle courant atteint au moins `required`. */
  can: (required: Role) => boolean
}

const RANK: Record<Role, number> = { lecteur: 10, commercial: 20, admin: 30 }

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    // Un jeton stocké peut avoir expiré : on le vérifie auprès de l'API
    // plutôt que de faire confiance à sa seule présence.
    if (!getToken()) {
      setLoading(false)
      return
    }
    api
      .me()
      .then((me) => setUser({ email: me.email, role: me.role as Role, full_name: me.full_name }))
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    setError('')
    try {
      const token = await api.login(email, password)
      setUser({ email: token.email, role: token.role, full_name: '' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connexion impossible.')
      throw err
    }
  }, [])

  const logout = useCallback(() => {
    api.logout()
    setUser(null)
  }, [])

  const can = useCallback(
    (required: Role) => (user ? RANK[user.role] >= RANK[required] : false),
    [user],
  )

  const value = useMemo(
    () => ({ user, loading, error, login, logout, can }),
    [user, loading, error, login, logout, can],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth doit être utilisé dans un AuthProvider.')
  return context
}
