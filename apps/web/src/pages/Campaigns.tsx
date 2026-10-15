import { useEffect, useState } from 'react'

import { Badge, Banner, EmptyState, Loading, formatDate } from '@/components/ui'
import { api } from '@/lib/api'
import type { Campaign, Product, Template } from '@/lib/types'

export function CampaignsPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api.products(), api.campaigns(), api.templates()])
      .then(([p, c, t]) => {
        setProducts(p)
        setCampaigns(c)
        setTemplates(t)
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <Loading />
  if (error) return <Banner tone="danger">{error}</Banner>

  return (
    <>
      <div className="page-header">
        <h2>Campagnes</h2>
        <p>
          Chaque campagne appartient à un seul produit. Les gabarits de message sont eux aussi
          cloisonnés : un gabarit Maintrix ne peut pas servir à EduCore.
        </p>
      </div>

      {products.map((product) => {
        const productCampaigns = campaigns.filter((c) => c.product_id === product.id)
        const productTemplates = templates.filter((t) => t.product_id === product.id)
        return (
          <section className="card" key={product.id}>
            <h3 style={{ marginTop: 0 }}>{product.name}</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              {product.sector} — {product.description}
            </p>

            {productCampaigns.length === 0 ? (
              <EmptyState
                title="Aucune campagne"
                description={`Aucune campagne n’est encore définie pour ${product.name}.`}
              />
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Campagne</th>
                    <th>Objectif</th>
                    <th>Régime</th>
                    <th>Statut</th>
                    <th>Créée le</th>
                  </tr>
                </thead>
                <tbody>
                  {productCampaigns.map((campaign) => (
                    <tr key={campaign.id}>
                      <td>
                        <strong>{campaign.name}</strong>
                        <div className="muted mono">{campaign.slug}</div>
                      </td>
                      <td className="muted">{campaign.objective || '—'}</td>
                      <td>
                        <Badge tone="info">{campaign.legal_regime}</Badge>
                      </td>
                      <td>
                        <Badge tone={campaign.status === 'active' ? 'ok' : 'neutral'}>
                          {campaign.status}
                        </Badge>
                      </td>
                      <td className="muted">{formatDate(campaign.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {productTemplates.length ? (
              <>
                <h4>Gabarits de message</h4>
                <ul className="muted" style={{ paddingLeft: 18, marginBottom: 0 }}>
                  {productTemplates.map((template) => (
                    <li key={template.id}>
                      <strong>{template.name}</strong> —{' '}
                      {template.allowed_variables.length
                        ? `variables autorisées : ${template.allowed_variables.join(', ')}`
                        : 'aucune variable autorisée'}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </section>
        )
      })}
    </>
  )
}
