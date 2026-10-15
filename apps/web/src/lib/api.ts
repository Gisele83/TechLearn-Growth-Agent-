import { ApiError } from './types'
import type {
  Approval,
  AuditEvent,
  Campaign,
  Draft,
  GrowthReport,
  Health,
  IntegrationStatus,
  Paged,
  Product,
  ProductKPI,
  Prospect,
  ProspectDetail,
  SendResult,
  Suppression,
  Template,
  TokenResponse,
} from './types'

const TOKEN_KEY = 'growth-agent.token'
const BASE = '/api/v1'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* Navigation privée ou stockage bloqué : la session vit alors en mémoire. */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let response: Response
  try {
    response = await fetch(path.startsWith('/health') ? path : `${BASE}${path}`, {
      ...init,
      headers,
    })
  } catch {
    throw new ApiError(0, 'network_error', "L'API est injoignable.", 'Vérifier qu’elle est démarrée.')
  }

  if (response.status === 204) return undefined as T

  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const code = payload?.code ?? 'error'
    const detail = payload?.detail ?? `Erreur ${response.status}.`
    throw new ApiError(response.status, code, detail, payload?.hint ?? '')
  }
  return payload as T
}

function query(params: Record<string, string | number | undefined | null>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const serialized = search.toString()
  return serialized ? `?${serialized}` : ''
}

export const api = {
  health: () => request<Health>('/health'),

  login: async (email: string, password: string) => {
    const token = await request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    setToken(token.access_token)
    return token
  },
  logout: () => setToken(null),
  me: () => request<{ email: string; role: string; full_name: string }>('/auth/me'),

  products: () => request<Product[]>('/products'),
  campaigns: (productCode?: string) =>
    request<Campaign[]>(`/campaigns${query({ product_code: productCode })}`),

  prospects: (params: {
    product_code?: string
    stage?: string
    verification_status?: string
    min_score?: number
    search?: string
    limit?: number
    offset?: number
  }) => request<Paged<Prospect>>(`/prospects${query(params)}`),
  prospect: (id: string) => request<ProspectDetail>(`/prospects/${id}`),
  discover: (body: { product_code: string; campaign_id?: string; limit?: number }) =>
    request<{
      created: number
      updated: number
      rejected: number
      rejected_details: Array<Record<string, string>>
      provider: string
      warnings: string[]
    }>('/prospects/discover', { method: 'POST', body: JSON.stringify(body) }),
  qualify: (id: string) =>
    request<ProspectDetail['latest_assessment']>(`/prospects/${id}/qualify`, {
      method: 'POST',
      body: JSON.stringify({ use_llm: true }),
    }),

  templates: (productCode?: string) =>
    request<Template[]>(`/templates${query({ product_code: productCode })}`),
  drafts: (params: { status?: string; product_code?: string; limit?: number }) =>
    request<Draft[]>(`/drafts${query(params)}`),
  compose: (body: { prospect_id: string; template_id?: string; campaign_id?: string }) =>
    request<Draft>('/drafts', { method: 'POST', body: JSON.stringify(body) }),
  editDraft: (id: string, body: { subject?: string; body?: string }) =>
    request<Draft>(`/drafts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  send: (id: string) => request<SendResult>(`/drafts/${id}/send`, { method: 'POST' }),

  approvals: (productCode?: string) =>
    request<Approval[]>(`/approvals${query({ product_code: productCode })}`),
  requestApproval: (draftId: string, summary = '') =>
    request<Approval>('/approvals', {
      method: 'POST',
      body: JSON.stringify({ draft_id: draftId, summary }),
    }),
  decide: (approvalId: string, approve: boolean, comment = '') =>
    request<Approval>(`/approvals/${approvalId}/decision`, {
      method: 'POST',
      body: JSON.stringify({ approve, comment }),
    }),

  suppressions: (productCode?: string) =>
    request<Suppression[]>(`/suppressions${query({ product_code: productCode })}`),
  addSuppression: (body: { value: string; reason?: string; scope?: string; note?: string }) =>
    request<Suppression>('/suppressions', { method: 'POST', body: JSON.stringify(body) }),
  removeSuppression: (id: string) =>
    request<void>(`/suppressions/${id}`, { method: 'DELETE' }),

  kpis: (productCode?: string, windowDays = 90) =>
    request<ProductKPI[]>(`/kpis${query({ product_code: productCode, window_days: windowDays })}`),
  report: (windowDays = 90) =>
    request<GrowthReport>(`/report${query({ window_days: windowDays })}`),
  audit: (params: { object_type?: string; action?: string; limit?: number; offset?: number }) =>
    request<AuditEvent[]>(`/audit${query(params)}`),
  integrations: () => request<IntegrationStatus>('/system/integrations'),
  syncPilots: (productCode: string) =>
    request<{
      product_code: string
      adapter_mode: string
      read_only: boolean
      imported: number
      duplicates: number
      renewals_due: Array<Record<string, unknown>>
      warnings: string[]
    }>(`/pilots/${productCode}/sync`, { method: 'POST' }),
}
