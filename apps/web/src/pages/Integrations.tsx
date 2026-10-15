import { useEffect, useState } from 'react'

import { Badge, Banner, Loading } from '@/components/ui'
import { api } from '@/lib/api'
import type { Health, IntegrationStatus } from '@/lib/types'

export function IntegrationsPage() {
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [syncNotice, setSyncNotice] = useState('')

  useEffect(() => {
    Promise.all([api.integrations(), api.health()])
      .then(([i, h]) => {
        setIntegrations(i)
        setHealth(h)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function sync(productCode: string) {
    setError('')
    setSyncNotice('')
    try {
      const result = await api.syncPilots(productCode)
      setSyncNotice(
        `${productCode} : ${result.imported} événement(s) importé(s), ` +
          `${result.duplicates} déjà connu(s). Mode ${result.adapter_mode}, lecture seule.`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Synchronisation impossible.')
    }
  }

  if (loading) return <Loading />
  if (error) return <Banner tone="danger">{error}</Banner>
  if (!integrations) return null

  return (
    <>
      <div className="page-header">
        <h2>Connecteurs</h2>
        <p>
          Growth Agent n’accède à aucune base de Maintrix ni d’EduCore. Les deux plateformes
          ne sont consultées qu’en lecture, et seulement si une autorisation explicite a été
          accordée.
        </p>
      </div>

      <Banner tone="info">
        Aucun secret n’est affiché sur cette page : seule la présence d’un identifiant
        configuré est indiquée.
      </Banner>

      {syncNotice ? <Banner tone="ok">{syncNotice}</Banner> : null}

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Plateformes produits</h3>
        <table>
          <thead>
            <tr>
              <th>Plateforme</th>
              <th>Adaptateur</th>
              <th>État</th>
              <th>Accès</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {(['maintrix', 'educore'] as const).map((code) => {
              const entry = integrations[code] as Record<string, unknown> | undefined
              if (!entry) return null
              const active = Boolean(entry.active)
              return (
                <tr key={code}>
                  <td style={{ textTransform: 'capitalize' }}>{code}</td>
                  <td className="mono">{String(entry.adapter)}</td>
                  <td>
                    <Badge tone={active ? 'warn' : 'neutral'}>
                      {active ? 'Connecté (réel)' : 'Simulé'}
                    </Badge>
                  </td>
                  <td>
                    <Badge tone="ok">{String(entry.access)}</Badge>
                  </td>
                  <td>
                    <button onClick={() => sync(code)}>Lire les statuts de pilote</button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="muted" style={{ marginBottom: 0 }}>
          L’activation d’un connecteur réel suppose que les endpoints existent côté plateforme
          et qu’une autorisation écrite ait été obtenue. Voir
          <span className="mono"> docs/11-dependances-autorisation.md</span>.
        </p>
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Services externes</h3>
        <table>
          <thead>
            <tr>
              <th>Service</th>
              <th>Fournisseur</th>
              <th>Identifiant configuré</th>
              <th>Mode effectif</th>
            </tr>
          </thead>
          <tbody>
            {(['crm', 'llm', 'search', 'email'] as const).map((key) => {
              const entry = integrations[key] as Record<string, unknown> | undefined
              if (!entry) return null
              const mode = String(entry.effective_mode ?? entry.mode ?? '—')
              return (
                <tr key={key}>
                  <td style={{ textTransform: 'uppercase' }}>{key}</td>
                  <td className="mono">{String(entry.provider)}</td>
                  <td>{entry.credential_configured ? 'Oui' : 'Non'}</td>
                  <td>
                    <Badge tone={mode === 'réel' ? 'warn' : 'neutral'}>{mode}</Badge>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {integrations.email ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            Plafonds d’envoi :{' '}
            {String((integrations.email as Record<string, unknown>).max_per_contact_per_30_days)}{' '}
            message(s) par contact sur 30 jours,{' '}
            {String((integrations.email as Record<string, unknown>).max_per_campaign_per_day)} par
            campagne et par jour.
          </p>
        ) : null}
      </section>

      {health ? (
        <section className="card">
          <h3 style={{ marginTop: 0 }}>État du service</h3>
          <p className="muted" style={{ marginTop: 0 }}>
            Version {health.version} · environnement {health.environment} · base de données{' '}
            {health.database}
          </p>
          {health.warnings.length ? (
            <Banner tone="warn">{health.warnings.join(' ')}</Banner>
          ) : (
            <p className="muted" style={{ marginBottom: 0 }}>
              Aucun avertissement de configuration.
            </p>
          )}
        </section>
      ) : null}
    </>
  )
}
