import { useCallback, useEffect, useState } from 'react'

import {
  Banner,
  DraftStatusBadge,
  EmptyState,
  Loading,
  StylePanel,
  formatDate,
} from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Approval, Draft } from '@/lib/types'

export function ApprovalsPage() {
  const { user, can } = useAuth()
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [drafts, setDrafts] = useState<Record<string, Draft>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [comments, setComments] = useState<Record<string, string>>({})

  const load = useCallback(() => {
    setLoading(true)
    api
      .approvals()
      .then(async (items) => {
        setApprovals(items)
        const drafted = await Promise.all(
          items
            .filter((item) => item.draft_id)
            .map((item) =>
              api
                .drafts({ limit: 200 })
                .then((all) => all.find((d) => d.id === item.draft_id))
                .catch(() => undefined),
            ),
        )
        const map: Record<string, Draft> = {}
        for (const draft of drafted) if (draft) map[draft.id] = draft
        setDrafts(map)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(load, [load])

  async function decide(approval: Approval, approve: boolean) {
    setBusy(approval.id)
    setError('')
    setNotice('')
    try {
      await api.decide(approval.id, approve, comments[approval.id] ?? '')
      setNotice(approve ? 'Message approuvé.' : 'Message rejeté.')
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Décision impossible.')
    } finally {
      setBusy('')
    }
  }

  async function send(draftId: string) {
    setBusy(draftId)
    setError('')
    setNotice('')
    try {
      const result = await api.send(draftId)
      setNotice(
        result.sent
          ? result.simulated
            ? 'Envoi simulé : le message n’a pas quitté le serveur.'
            : 'Message remis au fournisseur de messagerie.'
          : `Envoi refusé — ${result.detail}`,
      )
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Envoi impossible.')
    } finally {
      setBusy('')
    }
  }

  if (loading) return <Loading what="Chargement de la file de validation" />

  return (
    <>
      <div className="page-header">
        <h2>Validation des messages</h2>
        <p>
          Aucun message ne part sans une validation humaine enregistrée. L’auteur d’une demande
          ne peut pas la valider lui-même.
        </p>
      </div>

      {error ? <Banner tone="danger">{error}</Banner> : null}
      {notice ? <Banner tone="ok">{notice}</Banner> : null}

      {approvals.length === 0 ? (
        <div className="card">
          <EmptyState
            title="Aucune demande en attente"
            description="Les brouillons soumis à validation apparaîtront ici."
          />
        </div>
      ) : (
        approvals.map((approval) => {
          const draft = approval.draft_id ? drafts[approval.draft_id] : undefined
          const isOwnRequest =
            user?.email.toLowerCase() === approval.requested_by.toLowerCase()

          return (
            <section className="card" key={approval.id}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <strong>{approval.summary || 'Message sortant'}</strong>
                  <div className="muted">
                    Demandé par {approval.requested_by} le {formatDate(approval.created_at)}
                    {approval.expires_at ? ` · expire le ${formatDate(approval.expires_at)}` : ''}
                  </div>
                </div>
                {draft ? <DraftStatusBadge status={draft.status} /> : null}
              </div>

              {draft ? (
                <>
                  <p className="muted" style={{ marginBottom: 4 }}>
                    Destinataire : <span className="mono">{draft.recipient_email}</span>
                  </p>
                  <p style={{ marginBottom: 4 }}>
                    <strong>{draft.subject}</strong>
                  </p>
                  <div className="message-preview">{draft.body}</div>

                  <StylePanel style={draft.style} />

                  {draft.evidence_refs.length ? (
                    <p className="muted">
                      Sources citées : {draft.evidence_refs.join(', ')}
                    </p>
                  ) : (
                    <p className="muted">Aucune source citée dans ce message.</p>
                  )}
                </>
              ) : (
                <p className="muted">Brouillon indisponible.</p>
              )}

              {isOwnRequest ? (
                <Banner tone="info">
                  Vous êtes l’auteur de cette demande : une autre personne doit la valider.
                </Banner>
              ) : null}

              {can('commercial') && !isOwnRequest ? (
                <>
                  <div className="field" style={{ marginTop: 12 }}>
                    <label htmlFor={`comment-${approval.id}`}>Commentaire (facultatif)</label>
                    <input
                      id={`comment-${approval.id}`}
                      value={comments[approval.id] ?? ''}
                      onChange={(e) =>
                        setComments((prev) => ({ ...prev, [approval.id]: e.target.value }))
                      }
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button
                      className="primary"
                      onClick={() => decide(approval, true)}
                      disabled={busy === approval.id}
                    >
                      Approuver
                    </button>
                    <button
                      className="danger"
                      onClick={() => decide(approval, false)}
                      disabled={busy === approval.id}
                    >
                      Rejeter
                    </button>
                  </div>
                </>
              ) : null}

              {draft?.status === 'approved' && can('operateur') ? (
                <div style={{ marginTop: 12 }}>
                  <button onClick={() => send(draft.id)} disabled={busy === draft.id}>
                    Envoyer
                  </button>
                </div>
              ) : null}
            </section>
          )
        })
      )}
    </>
  )
}
