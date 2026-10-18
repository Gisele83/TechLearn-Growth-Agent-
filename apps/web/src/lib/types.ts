/** Types partagés avec l'API. Reflètent les schémas Pydantic côté serveur. */

export type Role = 'admin' | 'commercial' | 'operateur' | 'lecteur'

export type VerificationStatus = 'unknown' | 'hypothesis' | 'verified' | 'rejected'

export type ProspectStage =
  | 'discovered'
  | 'qualified'
  | 'contacted'
  | 'replied'
  | 'demo_scheduled'
  | 'pilot'
  | 'won'
  | 'lost'
  | 'disqualified'

export type DraftStatus =
  | 'draft'
  | 'pending_approval'
  | 'approved'
  | 'rejected'
  | 'sent'
  | 'blocked'

export interface CurrentUser {
  email: string
  role: Role
  full_name: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in_minutes: number
  role: Role
  email: string
}

export interface Product {
  id: string
  code: string
  name: string
  sector: string
  description: string
}

export interface Campaign {
  id: string
  product_id: string
  icp_id: string | null
  slug: string
  name: string
  objective: string
  status: string
  legal_regime: string
  owner_email: string
  target_metrics: Record<string, number>
  created_at: string
}

export interface ProspectSource {
  id: string
  kind: string
  url: string
  title: string
  excerpt: string
  collected_at: string
  collected_by: string
  provider: string
  license_note: string
}

export interface Criterion {
  key: string
  label: string
  points: number
  max_points: number
  status: VerificationStatus
  evidence: string[]
  comment: string
}

export interface Assessment {
  id: string
  total_score: number
  max_score: number
  verification_status: VerificationStatus
  confidence: string
  criteria: Criterion[]
  rationale: string
  unknowns: string[]
  assessed_by: string
  model_reference: string
  created_at: string
}

export interface Prospect {
  id: string
  product_id: string
  campaign_id: string | null
  organization_name: string
  dedupe_key: string
  website: string | null
  domain: string | null
  country_code: string
  city: string | null
  sector: string | null
  headcount_estimate: number | null
  contact_full_name: string | null
  contact_role: string | null
  contact_email: string | null
  stage: ProspectStage
  verification_status: VerificationStatus
  legal_basis: string
  score: number | null
  notes: string
  discovered_by: string
  created_at: string
}

export interface ProspectDetail extends Prospect {
  sources: ProspectSource[]
  latest_assessment: Assessment | null
}

export interface Paged<T> {
  items: T[]
  meta: { total: number; limit: number; offset: number }
}

export interface StyleFinding {
  code: string
  /** `blocking` empêche la validation ; `warning` alerte seulement. */
  severity: 'blocking' | 'warning'
  label: string
  excerpt: string
  suggestion: string
}

export interface StyleReport {
  /** 0 à 100. Recalculé à chaque lecture, jamais stocké. */
  naturalness: number
  word_count: number
  sentence_count: number
  is_blocking: boolean
  findings: StyleFinding[]
}

export interface Draft {
  id: string
  product_id: string
  campaign_id: string | null
  prospect_id: string
  template_id: string | null
  channel: string
  recipient_email: string | null
  subject: string
  body: string
  language: string
  status: DraftStatus
  variables_used: Record<string, string>
  evidence_refs: string[]
  generated_by: string
  model_reference: string
  blocked_reason: string
  sent_at: string | null
  created_at: string
  style: StyleReport | null
}

export interface Approval {
  id: string
  subject_type: string
  subject_id: string
  draft_id: string | null
  product_id: string | null
  requested_by: string
  summary: string
  decision: 'pending' | 'approved' | 'rejected' | 'expired'
  decided_by: string | null
  decided_at: string | null
  decision_comment: string
  expires_at: string | null
  created_at: string
}

export interface Funnel {
  discovered: number
  qualified: number
  contacted: number
  replied: number
  demos_scheduled: number
  demos_held: number
  pilots_started: number
  won: number
  lost: number
  opted_out: number
  blocked: number
}

export interface ProductKPI {
  product_code: string
  window_days: number
  computed_at: string
  window_start: string
  funnel: Funnel
  /** `null` quand le dénominateur est nul : absence de mesure, pas 0 %. */
  reply_rate: number | null
  demo_rate: number | null
  pilot_rate: number | null
  qualified_organizations: number
  verified_organizations: number
  unknown_organizations: number
  notes: string[]
}

export interface TargetGap {
  product_code: string
  metric: string
  target: number
  actual: number
  gap: number
  attained: boolean
  note: string
}

export interface GrowthReport {
  kpis: ProductKPI[]
  target_gaps: TargetGap[]
  recommendations: string[]
}

/**
 * Une cohorte de départ : les organisations contactées le même mois, et ce
 * qu'elles sont devenues.
 */
export interface CohortRow {
  /** Mois du premier envoi réel, au format `AAAA-MM`. */
  cohort: string
  counts: Record<string, number>
  /** `null` quand la cohorte n'a aucun contact : absence de mesure, pas 0 %. */
  conversion: Record<string, number | null>
  /**
   * Par étape, combien de ces prospects reposent sur un événement que le
   * système a constaté lui-même, et non sur une saisie.
   */
  observed: Record<string, number>
  /** Faux sous 30 jours : les conversions n'ont pas eu le temps de survenir. */
  mature: boolean
  note: string
}

export interface CohortReport {
  product_code: string
  campaign_slug: string
  computed_at: string
  cohorts: CohortRow[]
  /** Totaux par origine : `observed` / `declared`. */
  by_source: Record<string, number>
  /** Faits comptés à côté de l'entonnoir : rendez-vous manqués, oppositions. */
  side_facts: Record<string, number>
  median_days: Record<string, number | null>
  loss_reasons: Record<string, number>
  blocked_by_reason: Record<string, number>
  open_pipeline: Record<string, number>
  overdue_by_owner: Array<{ owner_email: string; overdue: number }>
  notes: string[]
}

export interface AuditEvent {
  id: string
  occurred_at: string
  actor: string
  actor_role: string
  action: string
  object_type: string
  object_id: string | null
  product_code: string
  outcome: string
  metadata_json: Record<string, unknown>
}

export interface Suppression {
  id: string
  product_id: string | null
  scope: string
  value_type: string
  normalized_value: string
  reason: string
  source: string
  note: string
  effective_from: string
  created_at: string
}

export interface Template {
  id: string
  product_id: string
  slug: string
  name: string
  channel: string
  language: string
  subject_template: string
  body_template: string
  allowed_variables: string[]
  is_active: boolean
}

export interface SendResult {
  sent: boolean
  simulated: boolean
  reason_code: string
  detail: string
  message_id: string
}

export interface IntegrationStatus {
  [key: string]: Record<string, unknown>
}

export interface Health {
  status: string
  environment: string
  version: string
  database: string
  connectors: Record<string, string>
  warnings: string[]
}

/** Erreur applicative renvoyée par l'API : { code, detail, hint }. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly hint: string = '',
  ) {
    super(message)
    this.name = 'ApiError'
  }
}
