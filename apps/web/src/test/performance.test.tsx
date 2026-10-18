/**
 * Page « Performance commerciale ».
 *
 * Ce qui est éprouvé ici n'est pas « la page affiche des chiffres » mais
 * « la page n'en fait pas dire plus qu'ils n'en disent » : pas de moyenne
 * cumulée, pas de 0 % là où il n'y a pas de mesure, pas de cohorte jeune
 * masquée, pas de volume déclaré présenté comme constaté, et aucun chiffre
 * affiché quand l'API a refusé de répondre.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { Measured, PerformancePage, cohortLabel } from '@/pages/Performance'
import type { CohortReport, Role } from '@/lib/types'
import { ApiError } from '@/lib/types'

const { produits, campagnes, cohortes } = vi.hoisted(() => ({
  produits: vi.fn(),
  campagnes: vi.fn(),
  cohortes: vi.fn(),
}))

let role: Role = 'commercial'

vi.mock('@/lib/api', () => ({
  api: { products: produits, campaigns: campagnes, cohorts: cohortes },
}))

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({
    user: { email: 'direction@techlearn-saem.com', role, full_name: '' },
    loading: false,
    error: '',
    login: vi.fn(),
    logout: vi.fn(),
    can: (requis: Role) =>
      ({ lecteur: 10, operateur: 20, commercial: 30, admin: 40 })[role] >=
      ({ lecteur: 10, operateur: 20, commercial: 30, admin: 40 })[requis],
  }),
}))

const PRODUITS = [
  { id: 'p1', code: 'educore', name: 'EduCore', sector: 'Éducation', description: '' },
  { id: 'p2', code: 'maintrix', name: 'Maintrix', sector: 'Maintenance', description: '' },
]

const CAMPAGNES = [
  {
    id: 'c1',
    product_id: 'p1',
    icp_id: null,
    slug: 'rentree-2026',
    name: 'Rentrée 2026',
    objective: '',
    status: 'active',
    legal_regime: '',
    owner_email: '',
    target_metrics: {},
    created_at: '2026-08-01T00:00:00Z',
  },
]

function rapport(surcharges: Partial<CohortReport> = {}): CohortReport {
  return {
    product_code: 'educore',
    campaign_slug: '',
    computed_at: '2026-10-18T09:00:00Z',
    cohorts: [
      {
        // Cohorte mûre : 8 contactés, 4 réponses, 2 démonstrations.
        cohort: '2026-08',
        counts: {
          contacted: 8,
          replied: 4,
          demo_scheduled: 3,
          demo_held: 2,
          pilot_started: 1,
          won: 1,
        },
        conversion: {
          replied: 0.5,
          demo_scheduled: 0.375,
          demo_held: 0.25,
          pilot_started: 0.125,
          won: 0.125,
        },
        // Les envois sont constatés, tout le reste est saisi à la main.
        observed: {
          contacted: 8,
          replied: 0,
          demo_scheduled: 0,
          demo_held: 0,
          pilot_started: 0,
          won: 0,
        },
        mature: true,
        note: '',
      },
      {
        // Cohorte du mois courant : aucune conversion, et c'est normal.
        cohort: '2026-10',
        counts: {
          contacted: 6,
          replied: 1,
          demo_scheduled: 0,
          demo_held: 0,
          pilot_started: 0,
          won: 0,
        },
        conversion: {
          replied: 0.1667,
          demo_scheduled: 0,
          demo_held: 0,
          pilot_started: 0,
          won: 0,
        },
        observed: {
          contacted: 6,
          replied: 0,
          demo_scheduled: 0,
          demo_held: 0,
          pilot_started: 0,
          won: 0,
        },
        mature: false,
        note: 'Cohorte de moins de 30 jours.',
      },
    ],
    by_source: { observed: 14, declared: 12 },
    side_facts: { demo_no_show: 2, opted_out: 1 },
    median_days: {
      contacted_to_replied: 3.5,
      replied_to_demo_scheduled: 5,
      demo_scheduled_to_demo_held: 2,
      demo_held_to_pilot_started: null,
      pilot_started_to_won: null,
    },
    loss_reasons: { no_budget: 2, non_renseigne: 1 },
    blocked_by_reason: { suppressed: 3, stale_inbox: 1 },
    open_pipeline: { open: 4, demo: 2, won: 1, lost: 3 },
    overdue_by_owner: [{ owner_email: 'awa@techlearn-saem.com', overdue: 3 }],
    notes: ['Cohorte(s) de moins de 30 jours : 2026-10.'],
    ...surcharges,
  }
}

async function afficher(reponse: unknown = rapport()) {
  cohortes.mockImplementation(() =>
    reponse instanceof Error ? Promise.reject(reponse) : Promise.resolve(reponse),
  )
  render(<PerformancePage />)
  await waitFor(() => expect(cohortes).toHaveBeenCalled())
}

/** Le tableau portant un en-tête donné. */
function tableauDe(titre: string): HTMLElement {
  const section = screen.getByRole('heading', { name: titre }).closest('section')
  if (!section) throw new Error(`Section introuvable : ${titre}`)
  return within(section).getByRole('table')
}

beforeEach(() => {
  role = 'commercial'
  vi.clearAllMocks()
  produits.mockResolvedValue(PRODUITS)
  campagnes.mockResolvedValue(CAMPAGNES)
})

// --- Lecture par cohorte ---------------------------------------------------

describe('volumes par cohorte', () => {
  it('donne une ligne à chaque cohorte, la plus récente comprise', async () => {
    await afficher()
    const lignes = within(tableauDe('Volumes par cohorte')).getAllByRole('row')
    // En-tête + deux cohortes.
    expect(lignes).toHaveLength(3)
    expect(lignes[1]).toHaveTextContent('août 2026')
    expect(lignes[2]).toHaveTextContent('octobre 2026')
  })

  it('signale une cohorte de moins de 30 jours sans la masquer', async () => {
    // La masquer effacerait le travail du mois en cours ; l'afficher sans
    // avertir inviterait à conclure d'une baisse qui n'est qu'un manque de recul.
    await afficher()
    const lignes = within(tableauDe('Volumes par cohorte')).getAllByRole('row')
    expect(lignes[2]).toHaveTextContent('octobre 2026')
    expect(lignes[2]).toHaveTextContent('moins de 30 jours')
    expect(lignes[1]).not.toHaveTextContent('moins de 30 jours')
  })

  it('distingue un volume constaté d’un volume saisi à la main', async () => {
    await afficher()
    const aout = within(tableauDe('Volumes par cohorte')).getAllByRole('row')[1]!
    const cellules = within(aout).getAllByRole('cell')
    // Contactés : le système a lui-même envoyé les 8 messages.
    expect(cellules[1]).toHaveTextContent('8')
    expect(cellules[1]).toHaveTextContent('constaté')
    // Démos tenues : les 2 viennent d'une saisie, rien ne les atteste.
    expect(cellules[4]).toHaveTextContent('2')
    expect(cellules[4]).toHaveTextContent('déclaré')
  })

  it('annonce la part saisie à la main en haut de page', async () => {
    await afficher()
    expect(screen.getByText(/Origine des chiffres/)).toBeInTheDocument()
    expect(screen.getByText(/12 saisi\(s\) par une personne/)).toBeInTheDocument()
  })
})

describe('taux de conversion', () => {
  it('rapporte chaque taux aux contactés de sa propre cohorte', async () => {
    await afficher()
    const lignes = within(tableauDe('Taux de conversion, cohorte par cohorte')).getAllByRole('row')
    expect(lignes[1]).toHaveTextContent('50 %') // août : 4 réponses sur 8
    expect(lignes[2]).toHaveTextContent('16.7 %') // octobre : 1 sur 6
  })

  it('n’affiche aucune moyenne cumulée', async () => {
    // Cumulé, on lirait 5 réponses sur 14, soit 35,7 % — un chiffre qu'aucune
    // des deux cohortes n'a produit, et qui masque l'écart entre elles.
    await afficher()
    expect(screen.queryByText(/35[.,]7/)).not.toBeInTheDocument()
    expect(screen.getByText(/Aucune moyenne cumulée/)).toBeInTheDocument()
  })

  it('écrit un tiret, et non 0 %, quand la cohorte n’a aucun contact', async () => {
    await afficher(
      rapport({
        cohorts: [
          {
            cohort: '2026-09',
            counts: { contacted: 0, replied: 0, demo_scheduled: 0, demo_held: 0, pilot_started: 0, won: 0 },
            conversion: {
              replied: null,
              demo_scheduled: null,
              demo_held: null,
              pilot_started: null,
              won: null,
            },
            observed: { contacted: 0, replied: 0, demo_scheduled: 0, demo_held: 0, pilot_started: 0, won: 0 },
            mature: true,
            note: '',
          },
        ],
      }),
    )
    const ligne = within(tableauDe('Taux de conversion, cohorte par cohorte')).getAllByRole('row')[1]!
    expect(ligne).not.toHaveTextContent('0 %')
    expect(within(ligne).getAllByTitle('Aucun contact dans cette cohorte').length).toBeGreaterThan(0)
  })
})

describe('délais, faits annexes et motifs', () => {
  it('montre les médianes et dit « donnée insuffisante » plutôt que zéro', async () => {
    await afficher()
    expect(screen.getByText('3.5 j')).toBeInTheDocument()
    expect(screen.getAllByText('Étape jamais franchie').length).toBe(2)
  })

  it('compte les rendez-vous manqués à part des démonstrations', async () => {
    await afficher()
    const section = screen
      .getByRole('heading', { name: /Ce qui n’apparaît pas dans l’entonnoir/ })
      .closest('section')!
    expect(within(section).getByText('Rendez-vous manqués')).toBeInTheDocument()
    expect(within(section).getByText('2')).toBeInTheDocument()
  })

  it('affiche les motifs de perte, dont ceux non renseignés', async () => {
    await afficher()
    const tableau = tableauDe('Motifs de perte')
    expect(tableau).toHaveTextContent('Pas de budget')
    expect(tableau).toHaveTextContent('Motif non renseigné')
  })

  it('traduit les motifs de blocage en langage lisible', async () => {
    await afficher()
    const tableau = tableauDe('Envois bloqués, par motif')
    expect(tableau).toHaveTextContent('Opposition enregistrée')
    expect(tableau).toHaveTextContent('Boîte de réponse non relevée')
  })

  it('attribue les actions en retard à une personne', async () => {
    await afficher()
    expect(tableauDe('Actions en retard, par personne')).toHaveTextContent(
      'awa@techlearn-saem.com',
    )
  })
})

// --- Filtres ---------------------------------------------------------------

describe('filtres', () => {
  it('interroge l’API avec le produit, la période et la campagne choisis', async () => {
    await afficher()
    cohortes.mockClear()

    await userEvent.selectOptions(screen.getByLabelText('Période'), '12')
    await waitFor(() => expect(cohortes).toHaveBeenCalled())
    expect(cohortes.mock.calls.at(-1)![0]).toMatchObject({ product_code: 'educore', months: 12 })

    await userEvent.selectOptions(screen.getByLabelText('Campagne'), 'rentree-2026')
    await waitFor(() =>
      expect(cohortes.mock.calls.at(-1)![0]).toMatchObject({ campaign_slug: 'rentree-2026' }),
    )
  })

  it('oublie la campagne quand on change de produit', async () => {
    // Un créneau d'EduCore n'existe pas sous Maintrix : le garder
    // sélectionné produirait un « campagne inconnue » incompréhensible.
    await afficher()
    await userEvent.selectOptions(screen.getByLabelText('Campagne'), 'rentree-2026')
    await waitFor(() =>
      expect(cohortes.mock.calls.at(-1)![0]).toMatchObject({ campaign_slug: 'rentree-2026' }),
    )

    await userEvent.selectOptions(screen.getByLabelText('Produit'), 'maintrix')
    await waitFor(() =>
      expect(cohortes.mock.calls.at(-1)![0]).toMatchObject({
        product_code: 'maintrix',
        campaign_slug: undefined,
      }),
    )
  })

  it('restreint l’affichage à une cohorte sans relancer la requête', async () => {
    await afficher()
    cohortes.mockClear()

    await userEvent.selectOptions(screen.getByLabelText('Cohorte'), '2026-08')

    const lignes = within(tableauDe('Volumes par cohorte')).getAllByRole('row')
    expect(lignes).toHaveLength(2)
    expect(lignes[1]).toHaveTextContent('août 2026')
    expect(cohortes).not.toHaveBeenCalled()
  })
})

// --- États dégradés --------------------------------------------------------

describe('états vides et erreurs', () => {
  it('explique l’absence de cohorte au lieu d’afficher un tableau vide', async () => {
    await afficher(rapport({ cohorts: [], notes: ['Aucun envoi réel sur la période.'] }))
    expect(screen.getByText('Aucune cohorte sur la période')).toBeInTheDocument()
  })

  it('n’affiche aucun chiffre quand l’API échoue', async () => {
    // Montrer d'anciens chiffres ferait prendre une décision sur un état
    // dépassé : c'est pire que de ne rien montrer.
    await afficher(new ApiError(500, 'error', 'Base de données injoignable.'))
    expect(screen.getByRole('alert')).toHaveTextContent('Base de données injoignable.')
    expect(screen.getByText('Indicateurs indisponibles')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('propose de réessayer après une erreur', async () => {
    await afficher(new ApiError(500, 'error', 'Base de données injoignable.'))
    cohortes.mockResolvedValue(rapport())
    await userEvent.click(screen.getByRole('button', { name: 'Réessayer' }))
    await waitFor(() => expect(screen.getByText('Volumes par cohorte')).toBeInTheDocument())
  })

  it('distingue un refus d’accès d’une panne', async () => {
    await afficher(new ApiError(403, 'forbidden', 'Droits insuffisants.'))
    expect(screen.getByText('Accès refusé par l’API')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    // Un refus de droits n'est pas une panne : proposer « Réessayer »
    // enverrait l'utilisateur cliquer en boucle sans rien changer.
    expect(screen.queryByRole('button', { name: 'Réessayer' })).not.toBeInTheDocument()
  })
})

// --- Cohérence des indicateurs ---------------------------------------------

describe('cohérence des indicateurs', () => {
  it('n’affiche jamais plus de constatés que de comptés', async () => {
    // Une erreur d'agrégation côté API produirait « 9 constatés » pour 8
    // contactés : un chiffre impossible, et lu comme rassurant.
    await afficher()
    const lignes = within(tableauDe('Volumes par cohorte')).getAllByRole('row').slice(1)
    for (const ligne of lignes) {
      for (const cellule of within(ligne).getAllByRole('cell').slice(1)) {
        const texte = cellule.textContent ?? ''
        const compte = Number(texte.match(/^\d+/)?.[0] ?? 0)
        const constates = Number(texte.match(/(\d+) constatés?/)?.[1] ?? (texte.includes('constaté') ? compte : 0))
        expect(constates).toBeLessThanOrEqual(compte)
      }
    }
  })

  it('reprend telles quelles les mises en garde de l’API', async () => {
    // Les recalculer côté interface ferait diverger deux vérités.
    await afficher()
    expect(screen.getByText('Cohorte(s) de moins de 30 jours : 2026-10.')).toBeInTheDocument()
  })
})

// --- Contrôle d'accès ------------------------------------------------------

describe('contrôle d’accès', () => {
  it('refuse la page à un rôle inconnu de l’échelle', async () => {
    role = 'inconnu' as Role
    cohortes.mockResolvedValue(rapport())
    render(<PerformancePage />)
    expect(await screen.findByText('Accès non autorisé')).toBeInTheDocument()
  })

  it('ouvre la page à un compte en lecture seule', async () => {
    role = 'lecteur'
    await afficher()
    expect(screen.getByText('Volumes par cohorte')).toBeInTheDocument()
  })
})

// --- Briques isolées -------------------------------------------------------

describe('Measured', () => {
  it.each([
    [0, 0, ''],
    [3, 3, 'constaté'],
    [3, 0, 'déclaré'],
    [3, 1, '1 constaté'],
    [4, 2, '2 constatés'],
  ])('rend %i/%i comme « %s »', (compte, constates, attendu) => {
    const { container } = render(<Measured count={compte} observed={constates} />)
    if (attendu === '') expect(container.textContent).toBe(String(compte))
    else expect(container.textContent).toBe(`${compte}${attendu}`)
  })
})

describe('cohortLabel', () => {
  it('écrit le mois en toutes lettres', () => {
    expect(cohortLabel('2026-08')).toBe('août 2026')
  })

  it('rend la valeur brute quand elle est illisible', () => {
    expect(cohortLabel('pas-un-mois')).toBe('pas-un-mois')
  })
})
