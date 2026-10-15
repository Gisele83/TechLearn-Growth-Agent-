import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  Banner,
  DraftStatusBadge,
  EmptyState,
  ScoreBar,
  Stat,
  VerificationBadge,
  formatDate,
  formatPercent,
} from '@/components/ui'

describe('Stat', () => {
  it('affiche la valeur quand elle existe', () => {
    render(<Stat label="Contactées" value={12} />)
    expect(screen.getByText('12')).toBeInTheDocument()
  })

  it("annonce une donnée insuffisante plutôt qu'un zéro trompeur", () => {
    // Un taux sans dénominateur n'est pas une contre-performance de 0 % :
    // afficher « 0 » induirait le lecteur en erreur.
    render(<Stat label="Taux de réponse" value={null} suffix=" %" unavailableReason="Aucun envoi" />)
    expect(screen.getByText('Aucun envoi')).toBeInTheDocument()
    expect(screen.queryByText('0 %')).not.toBeInTheDocument()
  })
})

describe('formatPercent', () => {
  it('convertit un ratio en pourcentage à une décimale', () => {
    expect(formatPercent(0.1234)).toBe(12.3)
    expect(formatPercent(1)).toBe(100)
  })

  it('préserve null', () => {
    expect(formatPercent(null)).toBeNull()
  })
})

describe('formatDate', () => {
  it('rend une date lisible', () => {
    expect(formatDate('2026-10-15T09:30:00Z')).toContain('15/10/2026')
  })

  it('rend un tiret pour une date absente ou illisible', () => {
    expect(formatDate(null)).toBe('—')
    expect(formatDate('pas-une-date')).toBe('—')
  })
})

describe('VerificationBadge', () => {
  it.each([
    ['verified', 'Vérifié'],
    ['hypothesis', 'Hypothèse'],
    ['unknown', 'Inconnu'],
    ['rejected', 'Écarté'],
  ])('traduit le statut %s', (status, label) => {
    render(<VerificationBadge status={status} />)
    expect(screen.getByText(label)).toBeInTheDocument()
  })
})

describe('DraftStatusBadge', () => {
  it('distingue un brouillon bloqué d’un brouillon envoyé', () => {
    const { rerender } = render(<DraftStatusBadge status="blocked" />)
    expect(screen.getByText('Bloqué')).toBeInTheDocument()
    rerender(<DraftStatusBadge status="sent" />)
    expect(screen.getByText('Envoyé')).toBeInTheDocument()
  })
})

describe('ScoreBar', () => {
  it('expose le score aux technologies d’assistance', () => {
    render(<ScoreBar score={72} />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '72')
    expect(meter).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByText('72/100')).toBeInTheDocument()
  })

  it('dit « non évalué » plutôt que d’afficher une barre vide', () => {
    render(<ScoreBar score={null} />)
    expect(screen.getByText('non évalué')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })
})

describe('EmptyState', () => {
  it('explique pourquoi c’est vide et quoi faire', () => {
    render(<EmptyState title="Aucun prospect" description="Lancez une recherche." />)
    expect(screen.getByRole('heading', { name: 'Aucun prospect' })).toBeInTheDocument()
    expect(screen.getByText('Lancez une recherche.')).toBeInTheDocument()
  })
})

describe('Banner', () => {
  it('annonce une erreur comme une alerte', () => {
    render(<Banner tone="danger">Échec</Banner>)
    expect(screen.getByRole('alert')).toHaveTextContent('Échec')
  })

  it('annonce une information comme un statut', () => {
    render(<Banner tone="info">Information</Banner>)
    expect(screen.getByRole('status')).toHaveTextContent('Information')
  })
})
