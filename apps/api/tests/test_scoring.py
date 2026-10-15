"""Scoring explicable : barème, statuts et refus d'inventer."""

from __future__ import annotations

from growth_agent.models.enums import VerificationStatus
from growth_agent.services.scoring import (
    CRITERION_WEIGHTS,
    MAX_SCORE,
    ScoringInput,
    score_prospect,
)


def _complete_input(**overrides) -> ScoringInput:
    base = {
        "product_code": "educore",
        "organization_name": "Collège Les Palmiers",
        "sector": "Enseignement secondaire",
        "country_code": "CM",
        "headcount_estimate": 60,
        "domain": "palmiers.example.org",
        "contact_full_name": "Sylvie Ateba",
        "contact_role": "Chef d'établissement",
        "contact_email": "direction@palmiers.example.org",
        "need_quotes": ["améliorer la gestion administrative", "deux nouvelles sections"],
        "evidence_urls": ["https://palmiers.example.org/inscriptions"],
        "target_sectors": ["Enseignement secondaire"],
        "min_headcount": 20,
        "max_headcount": 500,
    }
    base.update(overrides)
    return ScoringInput(**base)


def test_bareme_matches_specification():
    """Le barème est celui du cahier des charges : 25/25/20/15/15."""
    assert CRITERION_WEIGHTS == {
        "sector_fit": 25,
        "documented_need": 25,
        "reachable_decision_maker": 20,
        "pilot_feasibility": 15,
        "potential": 15,
    }
    assert sum(CRITERION_WEIGHTS.values()) == MAX_SCORE == 100


def test_complete_prospect_scores_full_and_is_verified():
    result = score_prospect(_complete_input())
    assert result.total_score == 100
    assert result.verification_status == VerificationStatus.VERIFIED
    assert result.confidence == "high"
    assert result.unknowns == []


def test_empty_prospect_scores_zero_and_stays_unknown():
    """Aucune donnée ne vaut aucun point : on ne suppose jamais au bénéfice
    du prospect."""
    result = score_prospect(ScoringInput(product_code="maintrix", organization_name="Inconnue"))
    assert result.total_score == 0
    assert result.verification_status == VerificationStatus.UNKNOWN
    assert len(result.unknowns) == 5
    assert result.confidence == "low"


def test_score_is_deterministic():
    data = _complete_input()
    assert score_prospect(data).as_dict() == score_prospect(data).as_dict()


def test_every_criterion_is_explained():
    result = score_prospect(_complete_input())
    assert len(result.criteria) == 5
    for criterion in result.criteria:
        assert criterion.label
        assert criterion.comment, f"{criterion.key} sans justification"
        assert 0 <= criterion.points <= criterion.max_points


def test_need_without_source_is_only_a_hypothesis():
    result = score_prospect(_complete_input(evidence_urls=[]))
    need = next(c for c in result.criteria if c.key == "documented_need")
    assert need.status == VerificationStatus.HYPOTHESIS
    assert need.points < need.max_points


def test_need_without_quotes_is_unknown_not_zero_by_assumption():
    result = score_prospect(_complete_input(need_quotes=[]))
    need = next(c for c in result.criteria if c.key == "documented_need")
    assert need.status == VerificationStatus.UNKNOWN
    assert need.points == 0
    assert "Besoin documenté" in result.unknowns


def test_public_email_weakens_decision_maker_criterion():
    result = score_prospect(_complete_input(contact_email="directeur@gmail.com"))
    criterion = next(c for c in result.criteria if c.key == "reachable_decision_maker")
    assert criterion.status == VerificationStatus.HYPOTHESIS
    assert criterion.points < criterion.max_points


def test_contact_email_on_foreign_domain_is_not_verified():
    result = score_prospect(_complete_input(contact_email="contact@autre-domaine.example.org"))
    criterion = next(c for c in result.criteria if c.key == "reachable_decision_maker")
    assert criterion.status == VerificationStatus.HYPOTHESIS


def test_sector_outside_target_is_rejected_criterion():
    result = score_prospect(_complete_input(sector="Transport maritime"))
    criterion = next(c for c in result.criteria if c.key == "sector_fit")
    assert criterion.status == VerificationStatus.REJECTED
    assert criterion.points == 0


def test_exclusion_signal_rejects_the_whole_prospect():
    result = score_prospect(_complete_input(exclusion_signals=["Les Palmiers"]))
    assert result.verification_status == VerificationStatus.REJECTED
    assert "écarté" in result.rationale


def test_high_score_without_evidence_is_not_verified():
    """Un bon score bâti sur des hypothèses ne donne pas droit à la
    prospection : c'est le statut qui ouvre ce droit, pas le total."""
    result = score_prospect(
        _complete_input(evidence_urls=[], target_sectors=[], contact_email="d@gmail.com")
    )
    assert result.total_score > 0
    assert result.verification_status != VerificationStatus.VERIFIED


def test_headcount_above_target_is_scored_but_flagged():
    result = score_prospect(_complete_input(headcount_estimate=5000))
    criterion = next(c for c in result.criteria if c.key == "potential")
    assert criterion.points < criterion.max_points
    assert "cycle de vente" in criterion.comment


def test_rationale_reports_evidence_share():
    result = score_prospect(_complete_input())
    assert "% des points reposent sur des sources datées" in result.rationale
