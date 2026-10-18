import { useCallback, useEffect, useState } from 'react'

import { Badge, Banner, EmptyState, Loading, formatDate } from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Suppression } from '@/lib/types'

const REASON_LABELS: Record<string, string> = {
  opt_out: 'Opposition exprimée',
  bounce_hard: 'Adresse invalide',
  complaint: 'Plainte',
  legal_request: 'Demande juridique',
  manual: 'Ajout manuel',
  competitor: 'Concurrent',
}

export function SuppressionsPage() {
  const { can } = useAuth()
  const [entries, setEntries] = useState<Suppression[]>([])
  const [value, setValue] = useState('')
  const [note, setNote] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(() => {
    setLoading(true)
    api
      .suppressions()
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  async function add(event: React.FormEvent) {
    event.preventDefault()
    setError('')
    setNotice('')
    try {
      await api.addSuppression({ value, note, reason: 'opt_out', scope: 'global' })
      setValue('')
      setNote('')
      setNotice('Opposition enregistrée. Aucun message ne partira plus vers cette adresse.')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ajout impossible.')
    }
  }

  async function remove(id: string) {
    setError('')
    setNotice('')
    try {
      await api.removeSuppression(id)
      setNotice('Opposition retirée. L’opération est inscrite au journal d’audit.')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Retrait impossible.')
    }
  }

  return (
    <>
      <div className="page-header">
        <h2>Liste d’opposition</h2>
        <p>
          Une adresse ou un domaine inscrit ici ne reçoit plus aucun message, quel que soit le
          produit ou la campagne.
        </p>
      </div>

      {error ? <Banner tone="danger">{error}</Banner> : null}
      {notice ? <Banner tone="ok">{notice}</Banner> : null}

      {can('operateur') ? (
        <form className="card" onSubmit={add}>
          <h3 style={{ marginTop: 0 }}>Ajouter une opposition</h3>
          <div className="toolbar" style={{ marginBottom: 0 }}>
            <div className="field" style={{ flex: 1 }}>
              <label htmlFor="value">Adresse email ou domaine</label>
              <input
                id="value"
                required
                placeholder="contact@exemple.org ou exemple.org"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label htmlFor="note">Note</label>
              <input id="note" value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
            <button className="primary" type="submit">
              Ajouter
            </button>
          </div>
        </form>
      ) : null}

      <div className="card">
        {loading ? (
          <Loading />
        ) : entries.length === 0 ? (
          <EmptyState
            title="Aucune opposition enregistrée"
            description="Les demandes de désinscription reçues en réponse y seront ajoutées automatiquement."
          />
        ) : (
          <table>
            <thead>
              <tr>
                <th>Valeur</th>
                <th>Type</th>
                <th>Portée</th>
                <th>Motif</th>
                <th>Depuis</th>
                {can('admin') ? <th></th> : null}
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="mono">{entry.normalized_value}</td>
                  <td>{entry.value_type === 'domain' ? 'Domaine' : 'Adresse'}</td>
                  <td>
                    <Badge tone={entry.scope === 'global' ? 'danger' : 'warn'}>
                      {entry.scope === 'global' ? 'Tous produits' : 'Un produit'}
                    </Badge>
                  </td>
                  <td>{REASON_LABELS[entry.reason] ?? entry.reason}</td>
                  <td>{formatDate(entry.effective_from)}</td>
                  {can('admin') ? (
                    <td>
                      <button className="danger" onClick={() => remove(entry.id)}>
                        Retirer
                      </button>
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  )
}
