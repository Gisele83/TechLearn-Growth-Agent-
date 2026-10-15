import { useEffect, useState } from 'react'

import { Banner, EmptyState, Loading, Stat, formatPercent } from '@/components/ui'
import { api } from '@/lib/api'
import type { GrowthReport, Health } from '@/lib/types'

export function DashboardPage() {
  const [report, setReport] = useState<GrowthReport | null>(null)
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([api.report(90), api.health()])
      .then(([r, h]) => {
        setReport(r)
        setHealth(h)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading what="Calcul des indicateurs" />
  if (error) return <Banner tone="danger">{error}</Banner>
  if (!report) return null

  const simulated = health?.connectors.email === 'disabled'

  return (
    <>
      <div className="page-header">
        <h2>Tableau de bord</h2>
        <p>
          Indicateurs recalculés à partir des événements, sur 90 jours glissants.
          {report.kpis[0] ? ` Calculé le ${new Date(report.kpis[0].computed_at).toLocaleString('fr-FR')}.` : ''}
        </p>
      </div>

      {simulated ? (
        <Banner tone="warn">
          Mode simulé : l’envoi réel d’emails est désactivé. Les messages « envoyés » n’ont
          pas quitté le serveur.
        </Banner>
      ) : null}

      {health?.warnings.length ? (
        <Banner tone="warn">
          <strong>Configuration : </strong>
          {health.warnings.join(' ')}
        </Banner>
      ) : null}

      {report.kpis.map((kpi) => (
        <section className="card" key={kpi.product_code}>
          <h3 style={{ marginTop: 0, textTransform: 'capitalize' }}>{kpi.product_code}</h3>

          <div className="grid cols-4">
            <Stat label="Organisations qualifiées" value={kpi.qualified_organizations} />
            <Stat label="Dont vérifiées" value={kpi.verified_organizations} />
            <Stat label="Contactées" value={kpi.funnel.contacted} />
            <Stat
              label="Taux de réponse"
              value={formatPercent(kpi.reply_rate)}
              suffix=" %"
              unavailableReason="Aucun envoi"
            />
            <Stat label="Démos tenues" value={kpi.funnel.demos_held} />
            <Stat label="Pilotes démarrés" value={kpi.funnel.pilots_started} />
            <Stat label="Oppositions" value={kpi.funnel.opted_out} />
            <Stat label="Envois bloqués" value={kpi.funnel.blocked} />
          </div>

          {kpi.notes.length ? (
            <ul className="muted" style={{ marginBottom: 0 }}>
              {kpi.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          ) : null}
        </section>
      ))}

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Objectifs exploratoires à 90 jours</h3>
        <p className="muted" style={{ marginTop: 0 }}>
          Cibles de travail issues du cahier des charges. Elles ne constituent pas un engagement.
        </p>
        <table>
          <thead>
            <tr>
              <th>Produit</th>
              <th>Indicateur</th>
              <th className="num">Réalisé</th>
              <th className="num">Cible</th>
              <th className="num">Écart</th>
            </tr>
          </thead>
          <tbody>
            {report.target_gaps.map((gap) => (
              <tr key={`${gap.product_code}-${gap.metric}`}>
                <td style={{ textTransform: 'capitalize' }}>{gap.product_code}</td>
                <td>{METRIC_LABELS[gap.metric] ?? gap.metric}</td>
                <td className="num">{gap.actual}</td>
                <td className="num">{gap.target}</td>
                <td className="num">{gap.attained ? '✓' : `−${gap.gap}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h3 style={{ marginTop: 0 }}>Lecture des chiffres</h3>
        {report.recommendations.length === 0 ? (
          <EmptyState
            title="Rien à signaler"
            description="Aucun écart notable sur la période."
          />
        ) : (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {report.recommendations.map((item) => (
              <li key={item} style={{ marginBottom: 6 }}>
                {item}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  )
}

const METRIC_LABELS: Record<string, string> = {
  qualified_organizations: 'Organisations qualifiées',
  demos_held: 'Démonstrations tenues',
  pilots_started: 'Pilotes démarrés',
}
