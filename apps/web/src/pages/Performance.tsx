/**
 * Performance commerciale, lue par cohorte de départ.
 *
 * Trois partis pris, qui expliquent pourquoi cette page ne ressemble pas à
 * un tableau de bord ordinaire.
 *
 * **Aucune moyenne cumulée.** Un total mélange des organisations contactées
 * en janvier avec d'autres contactées hier : il monte toujours, et ne dit
 * jamais si la conversion s'améliore. Une moyenne de 50 % peut recouvrir
 * une cohorte à 100 % et une autre à 25 % — deux situations qui appellent
 * des décisions opposées. Chaque cohorte est donc lue pour elle-même.
 *
 * **Constaté et déclaré ne se mélangent pas.** Un envoi, le système l'a
 * fait et il en garde la trace. Une démonstration, quelqu'un l'a saisie
 * après coup. Les afficher côte à côte sans le dire laisserait croire que
 * les deux chiffres se valent.
 *
 * **Une cohorte trop jeune est signalée, jamais masquée.** La masquer
 * ferait disparaître le travail du mois en cours ; l'afficher sans
 * avertir inviterait à conclure d'une baisse qui n'est qu'un manque de
 * recul.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge, Banner, EmptyState, Loading, Stat, formatPercent } from '@/components/ui'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Campaign, CohortReport, CohortRow, Product } from '@/lib/types'
import { ApiError } from '@/lib/types'

/** Étapes du parcours, dans l'ordre où elles s'enchaînent. */
const STAGES: Array<{ key: string; label: string }> = [
  { key: 'contacted', label: 'Contactés' },
  { key: 'replied', label: 'Réponses' },
  { key: 'demo_scheduled', label: 'RDV planifiés' },
  { key: 'demo_held', label: 'Démos tenues' },
  { key: 'pilot_started', label: 'Pilotes activés' },
  { key: 'won', label: 'Conversions' },
]

/** Les taux se lisent par rapport aux contactés : la première étape n'en a pas. */
const RATE_STAGES = STAGES.slice(1)

const DELAY_LABELS: Record<string, string> = {
  contacted_to_replied: 'Contact → réponse',
  replied_to_demo_scheduled: 'Réponse → RDV planifié',
  demo_scheduled_to_demo_held: 'RDV planifié → démo tenue',
  demo_held_to_pilot_started: 'Démo tenue → pilote',
  pilot_started_to_won: 'Pilote → conversion',
}

const LOSS_LABELS: Record<string, string> = {
  no_budget: 'Pas de budget',
  no_need: 'Pas de besoin',
  competitor: 'Concurrent retenu',
  timing: 'Mauvais moment',
  no_response: 'Sans réponse',
  not_decision_maker: 'Interlocuteur non décisionnaire',
  other: 'Autre',
  non_renseigne: 'Motif non renseigné',
}

const BLOCK_LABELS: Record<string, string> = {
  suppressed: 'Opposition enregistrée',
  stale_inbox: 'Boîte de réponse non relevée',
  frequency_contact: 'Plafond par contact',
  frequency_campaign: 'Plafond quotidien de campagne',
  not_approved: 'Message non validé',
  duplicate: 'Doublon',
  already_sent: 'Déjà envoyé',
  no_recipient: 'Sans destinataire',
  provider_refused: 'Refusé par le fournisseur',
  uncertain: 'Issue inconnue',
}

const PERIODS = [
  { months: 3, label: '3 mois' },
  { months: 6, label: '6 mois' },
  { months: 12, label: '12 mois' },
  { months: 24, label: '24 mois' },
]

function label(dictionary: Record<string, string>, key: string): string {
  return dictionary[key] ?? key
}

/**
 * Un volume, et d'où vient la certitude qu'il est juste.
 *
 * « 4 » tout seul se lit comme un fait. Dire qu'aucune de ces 4
 * démonstrations n'a été constatée par le système change la façon de s'en
 * servir : leur absence ne prouverait rien non plus.
 */
export function Measured({ count, observed }: { count: number; observed: number }) {
  let origin = ''
  if (count > 0) {
    if (observed === 0) origin = 'déclaré'
    else if (observed >= count) origin = 'constaté'
    else origin = `${observed} constaté${observed > 1 ? 's' : ''}`
  }
  return (
    <>
      <div style={{ fontVariantNumeric: 'tabular-nums' }}>{count}</div>
      {origin ? (
        <div className="muted" style={{ fontSize: '0.72rem' }}>
          {origin}
        </div>
      ) : null}
    </>
  )
}

/** Mois `2026-08` → « août 2026 ». */
export function cohortLabel(cohort: string): string {
  const [year, month] = cohort.split('-')
  const date = new Date(Number(year), Number(month) - 1, 1)
  if (Number.isNaN(date.getTime())) return cohort
  return new Intl.DateTimeFormat('fr-FR', { month: 'long', year: 'numeric' }).format(date)
}

function CohortName({ row }: { row: CohortRow }) {
  return (
    <>
      {cohortLabel(row.cohort)}
      {row.mature ? null : (
        <>
          {' '}
          <Badge tone="warn">moins de 30 jours</Badge>
        </>
      )}
    </>
  )
}

/** Un tableau simple clé → nombre, trié du plus fréquent au moins fréquent. */
function CountTable({
  rows,
  head,
  labels,
  empty,
}: {
  rows: Record<string, number>
  head: string
  labels: Record<string, string>
  empty: string
}) {
  const entries = Object.entries(rows).sort((a, b) => b[1] - a[1])
  if (entries.length === 0) return <p className="muted">{empty}</p>
  return (
    <table>
      <thead>
        <tr>
          <th>{head}</th>
          <th className="num">Nombre</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([key, total]) => (
          <tr key={key}>
            <td>{label(labels, key)}</td>
            <td className="num">{total}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

export function PerformancePage() {
  const { can } = useAuth()
  const [products, setProducts] = useState<Product[]>([])
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [productCode, setProductCode] = useState('')
  const [campaignSlug, setCampaignSlug] = useState('')
  const [months, setMonths] = useState(6)
  const [cohort, setCohort] = useState('')
  const [report, setReport] = useState<CohortReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [denied, setDenied] = useState(false)

  useEffect(() => {
    api
      .products()
      .then((list) => {
        setProducts(list)
        setProductCode((current) => current || list[0]?.code || '')
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    // Changer de produit invalide la campagne choisie : un créneau de
    // Maintrix n'existe pas sous EduCore, et le laisser sélectionné
    // afficherait « campagne inconnue » sans que personne comprenne.
    setCampaignSlug('')
    if (!productCode) {
      setCampaigns([])
      return
    }
    api
      .campaigns(productCode)
      .then(setCampaigns)
      .catch(() => setCampaigns([]))
  }, [productCode])

  const load = useCallback(() => {
    if (!productCode) {
      setReport(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    setDenied(false)
    api
      .cohorts({
        product_code: productCode,
        campaign_slug: campaignSlug || undefined,
        months,
      })
      .then(setReport)
      .catch((err) => {
        setReport(null)
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          setDenied(true)
          return
        }
        setError(err instanceof Error ? err.message : 'Indicateurs indisponibles.')
      })
      .finally(() => setLoading(false))
  }, [productCode, campaignSlug, months])

  useEffect(load, [load])

  // Le filtre par cohorte restreint l'affichage ; les totaux du rapport
  // (motifs de perte, blocages, charge) restent ceux de la période, et
  // c'est dit à l'écran plutôt que laissé à deviner.
  const shownCohorts = useMemo(
    () => (report ? report.cohorts.filter((row) => !cohort || row.cohort === cohort) : []),
    [report, cohort],
  )

  const sources = report?.by_source ?? {}
  const declared = sources.declared ?? 0
  const observed = sources.observed ?? 0
  const totalEvents = declared + observed

  if (!can('lecteur')) {
    return (
      <EmptyState
        title="Accès non autorisé"
        description="Cette page est réservée aux comptes disposant d’un accès en lecture."
      />
    )
  }

  return (
    <>
      <div className="page-header">
        <h2>Performance commerciale</h2>
        <p>
          Lecture par cohorte de départ — le mois du premier envoi réel. Les répétitions en
          simulation ne forment pas de cohorte et n’apparaissent pas ici.
          {report ? ` Calculé le ${new Date(report.computed_at).toLocaleString('fr-FR')}.` : ''}
        </p>
      </div>

      <div className="toolbar">
        <div className="field">
          <label htmlFor="perf-produit">Produit</label>
          <select
            id="perf-produit"
            value={productCode}
            onChange={(event) => setProductCode(event.target.value)}
          >
            {products.length === 0 ? <option value="">—</option> : null}
            {products.map((product) => (
              <option key={product.id} value={product.code}>
                {product.name}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="perf-periode">Période</label>
          <select
            id="perf-periode"
            value={months}
            onChange={(event) => setMonths(Number(event.target.value))}
          >
            {PERIODS.map((period) => (
              <option key={period.months} value={period.months}>
                {period.label}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="perf-campagne">Campagne</label>
          <select
            id="perf-campagne"
            value={campaignSlug}
            onChange={(event) => setCampaignSlug(event.target.value)}
          >
            <option value="">Toutes</option>
            {campaigns.map((campaign) => (
              <option key={campaign.id} value={campaign.slug}>
                {campaign.name}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="perf-cohorte">Cohorte</label>
          <select
            id="perf-cohorte"
            value={cohort}
            onChange={(event) => setCohort(event.target.value)}
          >
            <option value="">Toutes</option>
            {(report?.cohorts ?? []).map((row) => (
              <option key={row.cohort} value={row.cohort}>
                {cohortLabel(row.cohort)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {denied ? (
        <EmptyState
          title="Accès refusé par l’API"
          description="Votre compte n’a pas le droit de lire ces indicateurs. Demandez l’accès à un administrateur ; aucune donnée n’est affichée en attendant."
        />
      ) : error ? (
        <>
          <Banner tone="danger">{error}</Banner>
          <EmptyState
            title="Indicateurs indisponibles"
            description="Aucun chiffre n’est affiché : en montrer d’anciens ferait prendre une décision sur un état dépassé."
            action={<button onClick={load}>Réessayer</button>}
          />
        </>
      ) : loading ? (
        <Loading what="Calcul des cohortes" />
      ) : !report ? (
        <EmptyState
          title="Choisir un produit"
          description="Les cohortes se lisent produit par produit : Maintrix et EduCore ne s’adressent pas aux mêmes organisations."
        />
      ) : (
        <>
          {totalEvents > 0 ? (
            <Banner tone={declared > observed ? 'warn' : 'info'}>
              <strong>Origine des chiffres : </strong>
              {observed} événement(s) constaté(s) par le système, {declared} saisi(s) par une
              personne. Un fait non saisi reste invisible : l’absence d’une démonstration dans ce
              tableau ne prouve pas qu’elle n’a pas eu lieu.
            </Banner>
          ) : null}

          {/* Les mises en garde de l'API sont regroupées. Empilées en quatre
              bandeaux jaunes, elles cessaient d'être lues — et c'est
              précisément ce qu'on ne peut pas se permettre ici. */}
          {report.notes.length > 0 ? (
            <Banner tone="warn">
              <strong>Points de vigilance</strong>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {report.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </Banner>
          ) : null}

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Volumes par cohorte</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Sous chaque volume, d’où vient la certitude : « constaté » par le système,
              « déclaré » par une saisie.
            </p>
            {shownCohorts.length === 0 ? (
              <EmptyState
                title="Aucune cohorte sur la période"
                description="Aucun envoi réel n’a été effectué : il n’existe donc aucune cohorte à comparer. Élargissez la période ou retirez le filtre de campagne."
              />
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Cohorte</th>
                    {STAGES.map((stage) => (
                      <th className="num" key={stage.key}>
                        {stage.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shownCohorts.map((row) => (
                    <tr key={row.cohort}>
                      <td>
                        <CohortName row={row} />
                      </td>
                      {STAGES.map((stage) => (
                        <td className="num" key={stage.key}>
                          <Measured
                            count={row.counts[stage.key] ?? 0}
                            observed={row.observed[stage.key] ?? 0}
                          />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          {shownCohorts.length > 0 ? (
            <section className="card">
              <h3 style={{ marginTop: 0 }}>Taux de conversion, cohorte par cohorte</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                Aucune moyenne cumulée n’est affichée : elle masquerait l’écart entre deux
                cohortes. Chaque taux est rapporté aux contactés de sa propre cohorte.
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Cohorte</th>
                    {RATE_STAGES.map((stage) => (
                      <th className="num" key={stage.key}>
                        {stage.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {shownCohorts.map((row) => (
                    <tr key={row.cohort}>
                      <td>
                        <CohortName row={row} />
                      </td>
                      {RATE_STAGES.map((stage) => {
                        const rate = formatPercent(row.conversion[stage.key] ?? null)
                        return (
                          <td className="num" key={stage.key}>
                            {rate === null ? (
                              <span className="muted" title="Aucun contact dans cette cohorte">
                                —
                              </span>
                            ) : (
                              `${rate} %`
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {shownCohorts.some((row) => !row.mature) ? (
                <p className="muted">
                  Les cohortes signalées « moins de 30 jours » sont affichées parce que les
                  masquer effacerait le travail en cours — mais leurs taux sont mécaniquement bas
                  et ne se comparent pas aux autres.
                </p>
              ) : null}
            </section>
          ) : null}

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Délais médians</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Médiane et non moyenne : un dossier qui traîne six mois déplace une moyenne et ne
              dit rien du cas courant.
            </p>
            <div className="grid cols-4">
              {Object.entries(DELAY_LABELS).map(([key, text]) => (
                <Stat
                  key={key}
                  label={text}
                  value={report.median_days[key] ?? null}
                  suffix=" j"
                  unavailableReason="Étape jamais franchie"
                />
              ))}
            </div>
          </section>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Ce qui n’apparaît pas dans l’entonnoir</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Un rendez-vous manqué n’est ni une démonstration tenue ni une absence de
              démonstration. Compté à part, il reste visible.
            </p>
            <div className="grid cols-4">
              <Stat
                label="Rendez-vous manqués"
                value={report.side_facts.demo_no_show ?? 0}
                unavailableReason="Aucun relevé"
              />
              <Stat
                label="Oppositions reçues"
                value={report.side_facts.opted_out ?? 0}
                unavailableReason="Aucune"
              />
              <Stat
                label="Envois bloqués"
                value={Object.values(report.blocked_by_reason).reduce((a, b) => a + b, 0)}
              />
              <Stat
                label="Dossiers ouverts"
                value={Object.entries(report.open_pipeline)
                  .filter(([stage]) => stage !== 'won' && stage !== 'lost')
                  .reduce((total, [, count]) => total + count, 0)}
              />
            </div>
          </section>

          <div className="grid cols-2">
            <section className="card">
              <h3 style={{ marginTop: 0 }}>Motifs de perte</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                Sur toute la période, sans filtre de cohorte : un motif ne se rattache pas à un
                mois de départ.
              </p>
              <CountTable
                rows={report.loss_reasons}
                head="Motif"
                labels={LOSS_LABELS}
                empty="Aucun dossier perdu sur la période."
              />
            </section>

            <section className="card">
              <h3 style={{ marginTop: 0 }}>Envois bloqués, par motif</h3>
              <p className="muted" style={{ marginTop: 0 }}>
                Un envoi bloqué est un garde-fou qui a joué, pas une panne.
              </p>
              <CountTable
                rows={report.blocked_by_reason}
                head="Motif"
                labels={BLOCK_LABELS}
                empty="Aucun envoi bloqué sur la période."
              />
            </section>
          </div>

          <section className="card">
            <h3 style={{ marginTop: 0 }}>Actions en retard, par personne</h3>
            <p className="muted" style={{ marginTop: 0 }}>
              Un total global masque celle qui croule.
            </p>
            {report.overdue_by_owner.length === 0 ? (
              <p className="muted">Aucune action en retard.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Responsable</th>
                    <th className="num">En retard</th>
                  </tr>
                </thead>
                <tbody>
                  {report.overdue_by_owner.map((entry) => (
                    <tr key={entry.owner_email}>
                      <td>{entry.owner_email}</td>
                      <td className="num">{entry.overdue}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  )
}
