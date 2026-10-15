import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, getToken, setToken } from '@/lib/api'
import { ApiError } from '@/lib/types'

function mockFetch(status: number, body: unknown, ok = status < 400) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  } as Response)
}

describe('client API', () => {
  beforeEach(() => {
    setToken(null)
    vi.restoreAllMocks()
  })

  afterEach(() => {
    setToken(null)
  })

  it('stocke le jeton après une connexion réussie', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(200, {
        access_token: 'jeton-test',
        token_type: 'bearer',
        expires_in_minutes: 720,
        role: 'commercial',
        email: 'a@b.example.org',
      }),
    )
    await api.login('a@b.example.org', 'motdepasse')
    expect(getToken()).toBe('jeton-test')
  })

  it('joint le jeton aux requêtes suivantes', async () => {
    setToken('jeton-test')
    const fetchMock = mockFetch(200, [])
    vi.stubGlobal('fetch', fetchMock)

    await api.products()

    const call = fetchMock.mock.calls[0]
    expect(call).toBeDefined()
    const headers = new Headers((call![1] as RequestInit).headers)
    expect(headers.get('Authorization')).toBe('Bearer jeton-test')
  })

  it('traduit une erreur applicative en ApiError exploitable', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(403, { code: 'permission_denied', detail: 'Rôle insuffisant.', hint: 'Demander admin.' }, false),
    )
    await expect(api.products()).rejects.toMatchObject({
      status: 403,
      code: 'permission_denied',
      message: 'Rôle insuffisant.',
      hint: 'Demander admin.',
    })
  })

  it('signale une API injoignable sans faire planter l’interface', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    await expect(api.products()).rejects.toBeInstanceOf(ApiError)
    await expect(api.products()).rejects.toMatchObject({ code: 'network_error' })
  })

  it('gère une réponse 204 sans corps', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 204 } as Response))
    await expect(api.removeSuppression('abc')).resolves.toBeUndefined()
  })

  it('n’envoie que les paramètres renseignés', async () => {
    const fetchMock = mockFetch(200, { items: [], meta: { total: 0, limit: 50, offset: 0 } })
    vi.stubGlobal('fetch', fetchMock)

    await api.prospects({ product_code: 'educore', search: '', limit: 50 })

    const url = String(fetchMock.mock.calls[0]?.[0])
    expect(url).toContain('product_code=educore')
    expect(url).toContain('limit=50')
    expect(url).not.toContain('search=')
  })

  it('efface le jeton à la déconnexion', () => {
    setToken('jeton-test')
    api.logout()
    expect(getToken()).toBeNull()
  })
})
