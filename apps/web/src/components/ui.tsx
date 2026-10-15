import type { ReactNode } from 'react'

/** Bandeau d'information, d'avertissement ou d'erreur. */
export function Banner({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn' | 'danger' | 'ok'
  children: ReactNode
}) {
  return (
    <div className={`banner ${tone}`} role={tone === 'danger' ? 'alert' : 'status'}>
      {children}
    </div>
  )
}

/** État vide explicite : on dit pourquoi c'est vide et quoi faire ensuite. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string
  description: string
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      <p>{description}</p>
      {action ? <div style={{ marginTop: 14 }}>{action}</div> : null}
    </div>
  )
}

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: 'ok' | 'warn' | 'danger' | 'neutral' | 'info'
  children: ReactNode
}) {
  return <span className={`badge ${tone}`}>{children}</span>
}

const VERIFICATION_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' }> = {
  verified: { label: 'Vérifié', tone: 'ok' },
  hypothesis: { label: 'Hypothèse', tone: 'warn' },
  unknown: { label: 'Inconnu', tone: 'neutral' },
  rejected: { label: 'Écarté', tone: 'danger' },
}

export function VerificationBadge({ status }: { status: string }) {
  const entry = VERIFICATION_LABELS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={entry.tone}>{entry.label}</Badge>
}

const STAGE_LABELS: Record<string, string> = {
  discovered: 'Découvert',
  qualified: 'Qualifié',
  contacted: 'Contacté',
  replied: 'A répondu',
  demo_scheduled: 'Démo planifiée',
  pilot: 'Pilote',
  won: 'Gagné',
  lost: 'Perdu',
  disqualified: 'Disqualifié',
}

export function StageBadge({ stage }: { stage: string }) {
  const tone = stage === 'won' || stage === 'pilot' ? 'ok' : stage === 'lost' || stage === 'disqualified' ? 'danger' : 'neutral'
  return <Badge tone={tone}>{STAGE_LABELS[stage] ?? stage}</Badge>
}

const DRAFT_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' | 'neutral' | 'info' }> = {
  draft: { label: 'Brouillon', tone: 'neutral' },
  pending_approval: { label: 'En attente de validation', tone: 'info' },
  approved: { label: 'Approuvé', tone: 'ok' },
  rejected: { label: 'Rejeté', tone: 'danger' },
  sent: { label: 'Envoyé', tone: 'ok' },
  blocked: { label: 'Bloqué', tone: 'danger' },
}

export function DraftStatusBadge({ status }: { status: string }) {
  const entry = DRAFT_LABELS[status] ?? { label: status, tone: 'neutral' as const }
  return <Badge tone={entry.tone}>{entry.label}</Badge>
}

/**
 * Indicateur chiffré.
 *
 * Quand la valeur est `null`, on affiche « donnée insuffisante » et non 0 :
 * une absence de mesure n'est pas une contre-performance.
 */
export function Stat({
  label,
  value,
  suffix = '',
  unavailableReason = 'Donnée insuffisante',
}: {
  label: string
  value: number | null
  suffix?: string
  unavailableReason?: string
}) {
  return (
    <div className="stat">
      <div className="label">{label}</div>
      {value === null ? (
        <div className="value unavailable" title={unavailableReason}>
          {unavailableReason}
        </div>
      ) : (
        <div className="value">
          {value}
          {suffix}
        </div>
      )}
    </div>
  )
}

export function ScoreBar({ score, max = 100 }: { score: number | null; max?: number }) {
  if (score === null) return <span className="muted">non évalué</span>
  const pct = Math.max(0, Math.min(100, Math.round((score / max) * 100)))
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div
        className="score-bar"
        role="meter"
        aria-valuenow={score}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-label={`Score ${score} sur ${max}`}
      >
        <div style={{ width: `${pct}%` }} />
      </div>
      <span className="mono">
        {score}/{max}
      </span>
    </div>
  )
}

export function Loading({ what = 'Chargement' }: { what?: string }) {
  return (
    <p className="muted" role="status" aria-live="polite">
      {what}…
    </p>
  )
}

export function formatPercent(rate: number | null): number | null {
  return rate === null ? null : Math.round(rate * 1000) / 10
}

export function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('fr-FR', {
    dateStyle: 'short',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(date)
}
