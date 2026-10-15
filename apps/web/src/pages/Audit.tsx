import { useCallback, useEffect, useState } from 'react'

import { Badge, Banner, EmptyState, Loading, formatDate } from '@/components/ui'
import { api } from '@/lib/api'
import type { AuditEvent } from '@/lib/types'

const ACTION_LABELS: Record<string, string> = {
  'auth.login': 'Connexion',
  'auth.login_failed': 'Échec de connexion',
  'market_scout.discovered': 'Recherche de prospects',
  'lead_intelligence.qualified': 'Qualification',
  'outreach_composer.drafted': 'Brouillon rédigé',
  'approval.requested': 'Validation demandée',
  'approval.approved': 'Validation accordée',
  'approval.rejected': 'Validation refusée',
  'approval.rejected_content_changed': 'Validation annulée (texte modifié)',
  'outreach.sent': 'Message envoyé',
  'outreach.send_blocked': 'Envoi bloqué',
  'suppression.added': 'Opposition ajoutée',
  'suppression.removed': 'Opposition retirée',
  'draft.edited': 'Brouillon modifié',
  'campaign.created': 'Campagne créée',
  'campaign.status_changed': 'Statut de campagne modifié',
  'icp.created': 'Profil cible créé',
  'template.created': 'Gabarit créé',
  'customer_success.pilot_sync': 'Synchronisation des pilotes',
  'growth_analyst.report': 'Rapport de performance',
  'proposal_assistant.quote_prepared': 'Devis préparé',
  'sales_coordinator.task_skipped': 'Tâche écartée',
}

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api
      .audit({ action: filter || undefined, limit: 200 })
      .then(setEvents)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [filter])

  useEffect(load, [load])

  return (
    <>
      <div className="page-header">
        <h2>Journal d’audit</h2>
        <p>
          Qui a fait quoi, sur quel objet et quand. Aucune donnée personnelle ni aucun secret
          n’y figure : les valeurs sensibles sont masquées à l’écriture.
        </p>
      </div>

      {error ? <Banner tone="danger">{error}</Banner> : null}

      <div className="toolbar">
        <div className="field" style={{ minWidth: 260 }}>
          <label htmlFor="action">Filtrer par action</label>
          <select id="action" value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">Toutes</option>
            {Object.entries(ACTION_LABELS).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        {loading ? (
          <Loading />
        ) : events.length === 0 ? (
          <EmptyState
            title="Aucune entrée"
            description="Les actions effectuées dans l’application apparaîtront ici."
          />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Horodatage (UTC)</th>
                <th>Acteur</th>
                <th>Action</th>
                <th>Objet</th>
                <th>Résultat</th>
              </tr>
            </thead>
            <tbody>
              {events.map((event) => (
                <tr key={event.id}>
                  <td className="mono">{formatDate(event.occurred_at)}</td>
                  <td>
                    {event.actor}
                    {event.actor_role ? <div className="muted">{event.actor_role}</div> : null}
                  </td>
                  <td>{ACTION_LABELS[event.action] ?? event.action}</td>
                  <td className="muted">{event.object_type}</td>
                  <td>
                    <Badge tone={event.outcome === 'success' ? 'ok' : event.outcome === 'blocked' ? 'warn' : 'danger'}>
                      {event.outcome === 'success'
                        ? 'Succès'
                        : event.outcome === 'blocked'
                          ? 'Bloqué'
                          : 'Échec'}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
