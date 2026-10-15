import { useState } from 'react'

import { Banner } from '@/components/ui'
import { useAuth } from '@/lib/auth'

export function LoginPage() {
  const { login, error } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    try {
      await login(email, password)
    } catch {
      /* Le message est déjà porté par le contexte. */
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="login-screen">
      <form className="card" onSubmit={onSubmit}>
        <h1 style={{ marginTop: 0, fontSize: '1.2rem' }}>TechLearn Growth Agent</h1>
        <p className="muted" style={{ marginTop: 0 }}>
          Plateforme commerciale Maintrix et EduCore.
        </p>

        {error ? <Banner tone="danger">{error}</Banner> : null}

        <div className="field">
          <label htmlFor="email">Adresse professionnelle</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor="password">Mot de passe</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button className="primary" type="submit" disabled={submitting} style={{ width: '100%' }}>
          {submitting ? 'Connexion…' : 'Se connecter'}
        </button>
      </form>
    </div>
  )
}
