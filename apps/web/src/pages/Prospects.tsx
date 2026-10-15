import { useCallback, useEffect, useState } from 'react'

import {
  Banner,
  EmptyState,
  Loading,
  ScoreBar,
  StageBadge,
  VerificationBadge,
  formatDate,
} from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Campaign, Product, Prospect, ProspectDetail } from '@/lib/types'

export function ProspectsPage() {
  const { can } = useAuth()
  const [products, setProducts] = useState<Product[]>([])
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [productCode, setProductCode] = useState('')
  const [status, setStatus] = useState('')
  const [search, setSearch] = useState('')
  const [prospects, setProspects] = useState<Prospect[]>([])
  const [total, setTotal] = useState(0)
  const [selected, setSelected] = useState<ProspectDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    api.products().then(setProducts).catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    if (!productCode) {
      setCampaigns([])
      return
    }
    api.campaigns(productCode).then(setCampaigns).catch(() => setCampaigns([]))
  }, [productCode])

  const load = useCallback(() => {
    setLoading(true)
    api
      .prospects({
        product_code: productCode || undefined,
        verification_status: status || undefined,
        search: search || undefined,
        limit: 100,
      })
      .then((page) => {
        setProspects(page.items)
        setTotal(page.meta.total)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [productCode, status, search])

  useEffect(load, [load])

  async function runDiscovery() {
    if (!productCode) return
    setBusy('discovery')
    setError('')
    setNotice('')
    try {
      const campaign = campaigns[0]
      const result = await api.discover({
        product_code: productCode,
        campaign_id: campaign?.id,
        limit: 25,
      })
      setNotice(
        `${result.created} organisation(s) ajoutée(s), ${result.updated} mise(s) à jour, ` +
          `${result.rejected} écartée(s) faute de provenance. Source : ${result.provider}.`,
      )
      load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Recherche impossible.')
    } finally {
      setBusy('')
    }
  }

  async function qualify(id: string) {
    setBusy(id)
    setError('')
    try {
      await api.qualify(id)
      load()
      if (selected?.id === id) setSelected(await api.prospect(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Qualification impossible.')
    } finally {
      setBusy('')
    }
  }

  async function openDetail(id: string) {
    setSelected(null)
    try {
      setSelected(await api.prospect(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Fiche indisponible.')
    }
  }

  return (
    <>
      <div className="page-header">
        <h2>Prospects</h2>
        <p>
          Chaque organisation porte ses sources et un score explicable. Un prospect sans source
          reste « inconnu » et ne peut pas être sollicité.
        </p>
      </div>

      {error ? <Banner tone="danger">{error}</Banner> : null}
      {notice ? <Banner tone="ok">{notice}</Banner> : null}

      <div className="toolbar">
        <div className="field">
          <label htmlFor="product">Produit</label>
          <select id="product" value={productCode} onChange={(e) => setProductCode(e.target.value)}>
            <option value="">Tous</option>
            {products.map((p) => (
              <option key={p.id} value={p.code}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="status">Vérification</label>
          <select id="status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Tous statuts</option>
            <option value="verified">Vérifié</option>
            <option value="hypothesis">Hypothèse</option>
            <option value="unknown">Inconnu</option>
            <option value="rejected">Écarté</option>
          </select>
        </div>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor="search">Recherche</label>
          <input
            id="search"
            type="search"
            placeholder="Nom d’organisation"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        {can('commercial') ? (
          <button
            className="primary"
            onClick={runDiscovery}
            disabled={!productCode || busy === 'discovery'}
            title={!productCode ? 'Choisir un produit d’abord' : undefined}
          >
            {busy === 'discovery' ? 'Recherche…' : 'Rechercher des prospects'}
          </button>
        ) : null}
      </div>

      <div className="card">
        {loading ? (
          <Loading what="Chargement des prospects" />
        ) : prospects.length === 0 ? (
          <EmptyState
            title="Aucun prospect"
            description={
              productCode
                ? 'Lancez une recherche pour constituer une première liste, ou importez un prospect avec sa source.'
                : 'Choisissez un produit puis lancez une recherche.'
            }
          />
        ) : (
          <>
            <p className="muted">{total} organisation(s).</p>
            <table>
              <caption className="muted" style={{ captionSide: 'bottom', textAlign: 'left', paddingTop: 8 }}>
                Score sur 100 : secteur 25, besoin documenté 25, décideur 20, faisabilité 15,
                potentiel 15.
              </caption>
              <thead>
                <tr>
                  <th>Organisation</th>
                  <th>Contact</th>
                  <th>Score</th>
                  <th>Vérification</th>
                  <th>Étape</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {prospects.map((prospect) => (
                  <tr key={prospect.id}>
                    <td>
                      <button
                        onClick={() => openDetail(prospect.id)}
                        style={{ border: 'none', background: 'none', padding: 0, textAlign: 'left', color: 'var(--accent)', cursor: 'pointer' }}
                      >
                        {prospect.organization_name}
                      </button>
                      <div className="muted">
                        {[prospect.city, prospect.country_code, prospect.sector]
                          .filter(Boolean)
                          .join(' · ')}
                      </div>
                    </td>
                    <td>
                      {prospect.contact_full_name ?? <span className="muted">non identifié</span>}
                      <div className="muted">{prospect.contact_role ?? ''}</div>
                    </td>
                    <td>
                      <ScoreBar score={prospect.score} />
                    </td>
                    <td>
                      <VerificationBadge status={prospect.verification_status} />
                    </td>
                    <td>
                      <StageBadge stage={prospect.stage} />
                    </td>
                    <td>
                      {can('commercial') ? (
                        <button onClick={() => qualify(prospect.id)} disabled={busy === prospect.id}>
                          {busy === prospect.id ? '…' : 'Qualifier'}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>

      {selected ? <ProspectDetailCard prospect={selected} /> : null}
    </>
  )
}

function ProspectDetailCard({ prospect }: { prospect: ProspectDetail }) {
  const assessment = prospect.latest_assessment

  return (
    <section className="card">
      <h3 style={{ marginTop: 0 }}>{prospect.organization_name}</h3>

      <div className="grid cols-2">
        <div>
          <h4 style={{ marginBottom: 6 }}>Sources ({prospect.sources.length})</h4>
          {prospect.sources.length === 0 ? (
            <p className="muted">
              Aucune source. Sans provenance, ce prospect ne peut pas être qualifié ni contacté.
            </p>
          ) : (
            <ul style={{ paddingLeft: 18, margin: 0 }}>
              {prospect.sources.map((source) => (
                <li key={source.id} style={{ marginBottom: 10 }}>
                  <a href={source.url} target="_blank" rel="noreferrer noopener">
                    {source.title || source.url}
                  </a>
                  <div className="muted">
                    {source.kind} · collecté le {formatDate(source.collected_at)} via{' '}
                    {source.provider}
                  </div>
                  {source.excerpt ? (
                    <div className="muted" style={{ fontStyle: 'italic' }}>
                      « {source.excerpt.slice(0, 220)} »
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <h4 style={{ marginBottom: 6 }}>Qualification</h4>
          {!assessment ? (
            <p className="muted">Pas encore qualifié.</p>
          ) : (
            <>
              <p style={{ marginTop: 0 }}>
                <ScoreBar score={assessment.total_score} max={assessment.max_score} />
              </p>
              <p className="muted" style={{ marginTop: 0 }}>
                {assessment.rationale}
              </p>
              {assessment.criteria.map((criterion) => (
                <div className="criterion" key={criterion.key}>
                  <div className="criterion-head">
                    <strong>{criterion.label}</strong>
                    <span className="mono">
                      {criterion.points}/{criterion.max_points}
                    </span>
                  </div>
                  <div className="muted">{criterion.comment}</div>
                  <div style={{ marginTop: 4 }}>
                    <VerificationBadge status={criterion.status} />
                  </div>
                  {criterion.evidence.length ? (
                    <ul>
                      {criterion.evidence.map((url) => (
                        <li key={url}>
                          <a href={url} target="_blank" rel="noreferrer noopener">
                            {url}
                          </a>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ))}
              {assessment.unknowns.length ? (
                <p className="muted">
                  <strong>Inconnues : </strong>
                  {assessment.unknowns.join(', ')}.
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </section>
  )
}
