import { NavLink, Navigate, Route, Routes } from 'react-router-dom'

import { Loading } from '@/components/ui'
import { useAuth } from '@/lib/auth'
import { ApprovalsPage } from '@/pages/Approvals'
import { AuditPage } from '@/pages/Audit'
import { CampaignsPage } from '@/pages/Campaigns'
import { DashboardPage } from '@/pages/Dashboard'
import { IntegrationsPage } from '@/pages/Integrations'
import { LoginPage } from '@/pages/Login'
import { ProspectsPage } from '@/pages/Prospects'
import { SuppressionsPage } from '@/pages/Suppressions'

const ROLE_LABELS: Record<string, string> = {
  admin: 'Administration',
  commercial: 'Commercial',
  lecteur: 'Lecture seule',
}

export function App() {
  const { user, loading, logout } = useAuth()

  if (loading) return <Loading what="Ouverture de la session" />
  if (!user) return <LoginPage />

  return (
    <div className="app">
      <a className="skip-link" href="#contenu">
        Aller au contenu
      </a>

      <aside className="sidebar">
        <h1>Growth Agent</h1>
        <p className="version">Maintrix &amp; EduCore</p>

        <nav aria-label="Navigation principale">
          <NavLink to="/" end>
            Tableau de bord
          </NavLink>
          <NavLink to="/campagnes">Campagnes</NavLink>
          <NavLink to="/prospects">Prospects</NavLink>
          <NavLink to="/validations">Validations</NavLink>
          <NavLink to="/oppositions">Oppositions</NavLink>
          <NavLink to="/audit">Journal d’audit</NavLink>
          <NavLink to="/connecteurs">Connecteurs</NavLink>
        </nav>

        <div className="user">
          <div>{user.email}</div>
          <div>{ROLE_LABELS[user.role] ?? user.role}</div>
          <button onClick={logout} style={{ marginTop: 8, width: '100%' }}>
            Se déconnecter
          </button>
        </div>
      </aside>

      <main className="main" id="contenu">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/campagnes" element={<CampaignsPage />} />
          <Route path="/prospects" element={<ProspectsPage />} />
          <Route path="/validations" element={<ApprovalsPage />} />
          <Route path="/oppositions" element={<SuppressionsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/connecteurs" element={<IntegrationsPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  )
}
