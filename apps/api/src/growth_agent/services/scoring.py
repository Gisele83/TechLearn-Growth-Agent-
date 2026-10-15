"""Scoring de qualification explicable, sur 100 points.

Barème imposé par le cahier des charges :
    secteur                 25
    besoin documenté        25
    décideur accessible     20
    faisabilité pilote      15
    potentiel               15

Trois principes gouvernent ce module :

1. **Rien n'est inventé.** Chaque point attribué cite la preuve qui le
   justifie. En l'absence de preuve, le critère vaut zéro et l'inconnue est
   consignée — on ne « suppose » jamais au bénéfice du prospect.

2. **Preuve, hypothèse, inconnu sont distingués.** Un signal lu dans une
   source publique datée est une preuve ; une déduction interne est une
   hypothèse, qui ne rapporte qu'une fraction des points ; le reste est
   inconnu.

3. **Le score ne décide pas.** Un prospect très bien noté dont les données
   sont majoritairement inconnues reste `unknown` et ne peut pas être
   contacté : c'est `verification_status`, pas le total, qui ouvre le droit à
   la prospection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from growth_agent.models.enums import VerificationStatus
from growth_agent.services.dedupe import (
    email_domain,
    is_public_email_domain,
    normalize_domain,
    normalize_email,
    strip_accents,
)

MAX_SCORE = 100

CRITERION_WEIGHTS: dict[str, int] = {
    "sector_fit": 25,
    "documented_need": 25,
    "reachable_decision_maker": 20,
    "pilot_feasibility": 15,
    "potential": 15,
}

# Un prospect dont une part trop faible du score repose sur des preuves reste
# « hypothèse » : il peut être travaillé, mais pas sollicité.
VERIFIED_EVIDENCE_RATIO = 0.5
VERIFIED_MIN_SCORE = 50

DECISION_MAKER_KEYWORDS: dict[str, tuple[str, ...]] = {
    "maintrix": (
        "maintenance",
        "technique",
        "production",
        "exploitation",
        "usine",
        "industriel",
        "operations",
        "qhse",
        "travaux",
        "methodes",
    ),
    "educore": (
        "directeur",
        "directrice",
        "chef d'etablissement",
        "proviseur",
        "promoteur",
        "administratif",
        "scolarite",
        "secretaire general",
        "intendant",
        "responsable pedagogique",
    ),
}

EXECUTIVE_KEYWORDS = (
    "directeur",
    "directrice",
    "responsable",
    "chef",
    "president",
    "gerant",
    "promoteur",
)


@dataclass(slots=True)
class CriterionScore:
    """Score d'un critère, avec sa justification."""

    key: str
    label: str
    points: int
    max_points: int
    status: str
    evidence: list[str] = field(default_factory=list)
    comment: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": self.points,
            "max_points": self.max_points,
            "status": self.status,
            "evidence": list(self.evidence),
            "comment": self.comment,
        }


@dataclass(slots=True)
class ScoringResult:
    total_score: int
    max_score: int
    criteria: list[CriterionScore]
    verification_status: str
    confidence: str
    unknowns: list[str]
    rationale: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_score": self.total_score,
            "max_score": self.max_score,
            "criteria": [c.as_dict() for c in self.criteria],
            "verification_status": self.verification_status,
            "confidence": self.confidence,
            "unknowns": list(self.unknowns),
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ScoringInput:
    """Faits sur lesquels le score est calculé.

    `evidence_urls` est la liste des sources datées rattachées au prospect.
    Sans elle, l'essentiel du barème reste à zéro — c'est voulu.
    """

    product_code: str
    organization_name: str
    sector: str | None = None
    country_code: str = "FR"
    headcount_estimate: int | None = None
    website: str | None = None
    domain: str | None = None
    contact_full_name: str | None = None
    contact_role: str | None = None
    contact_email: str | None = None
    need_quotes: list[str] = field(default_factory=list)
    evidence_urls: list[str] = field(default_factory=list)
    target_sectors: list[str] = field(default_factory=list)
    exclusion_signals: list[str] = field(default_factory=list)
    min_headcount: int | None = None
    max_headcount: int | None = None


def score_prospect(data: ScoringInput) -> ScoringResult:
    """Calcule le score. Fonction pure : mêmes entrées, même sortie."""
    criteria = [
        _score_sector(data),
        _score_need(data),
        _score_decision_maker(data),
        _score_pilot_feasibility(data),
        _score_potential(data),
    ]

    # Sans aucune source rattachée, rien n'est « vérifié » — pas même ce qui
    # semble établi par les données structurées, puisque ces données viennent
    # elles aussi de quelque part. On rétrograde donc tous les critères en
    # hypothèse. C'est ce qui empêche un prospect sans provenance d'obtenir le
    # statut `verified`, seul statut qui autorise la prospection.
    if not data.evidence_urls:
        for criterion in criteria:
            if criterion.status == VerificationStatus.VERIFIED:
                criterion.status = VerificationStatus.HYPOTHESIS
                criterion.comment = (
                    f"{criterion.comment} Aucune source rattachée : reste une hypothèse."
                ).strip()

    total = sum(c.points for c in criteria)
    evidence_points = sum(c.points for c in criteria if c.status == VerificationStatus.VERIFIED)
    unknowns = [c.label for c in criteria if c.status == VerificationStatus.UNKNOWN]

    evidence_ratio = (evidence_points / total) if total else 0.0
    if total >= VERIFIED_MIN_SCORE and evidence_ratio >= VERIFIED_EVIDENCE_RATIO:
        verification = VerificationStatus.VERIFIED
    elif evidence_points > 0 or total > 0:
        verification = VerificationStatus.HYPOTHESIS
    else:
        verification = VerificationStatus.UNKNOWN

    if _is_excluded(data):
        verification = VerificationStatus.REJECTED

    confidence = _confidence_from(total, evidence_ratio, len(unknowns))
    rationale = _build_rationale(criteria, total, evidence_ratio, verification, unknowns)

    return ScoringResult(
        total_score=total,
        max_score=MAX_SCORE,
        criteria=criteria,
        verification_status=verification,
        confidence=confidence,
        unknowns=unknowns,
        rationale=rationale,
    )


# --- Critères ----------------------------------------------------------------


def _score_sector(data: ScoringInput) -> CriterionScore:
    maximum = CRITERION_WEIGHTS["sector_fit"]
    if not data.sector:
        return CriterionScore(
            key="sector_fit",
            label="Adéquation sectorielle",
            points=0,
            max_points=maximum,
            status=VerificationStatus.UNKNOWN,
            comment="Secteur d'activité non renseigné.",
        )

    sector_norm = strip_accents(data.sector).lower()
    targets = [strip_accents(s).lower() for s in data.target_sectors if s]

    if targets:
        exact = [t for t in targets if t and (t in sector_norm or sector_norm in t)]
        if exact:
            return CriterionScore(
                key="sector_fit",
                label="Adéquation sectorielle",
                points=maximum,
                max_points=maximum,
                status=VerificationStatus.VERIFIED,
                evidence=data.evidence_urls[:2],
                comment=f"Secteur « {data.sector} » présent dans le profil cible.",
            )
        partial = [t for t in targets if _shares_token(t, sector_norm)]
        if partial:
            return CriterionScore(
                key="sector_fit",
                label="Adéquation sectorielle",
                points=round(maximum * 0.6),
                max_points=maximum,
                status=VerificationStatus.HYPOTHESIS,
                comment=(
                    f"Secteur « {data.sector} » proche du profil cible, sans correspondance exacte."
                ),
            )
        return CriterionScore(
            key="sector_fit",
            label="Adéquation sectorielle",
            points=0,
            max_points=maximum,
            status=VerificationStatus.REJECTED,
            comment=f"Secteur « {data.sector} » hors du profil cible.",
        )

    # Aucun profil cible défini : on ne peut ni valider ni écarter.
    return CriterionScore(
        key="sector_fit",
        label="Adéquation sectorielle",
        points=round(maximum * 0.4),
        max_points=maximum,
        status=VerificationStatus.HYPOTHESIS,
        comment="Aucun profil de client idéal défini : adéquation non vérifiable.",
    )


def _score_need(data: ScoringInput) -> CriterionScore:
    maximum = CRITERION_WEIGHTS["documented_need"]
    quotes = [q.strip() for q in data.need_quotes if q and q.strip()]
    if not quotes:
        return CriterionScore(
            key="documented_need",
            label="Besoin documenté",
            points=0,
            max_points=maximum,
            status=VerificationStatus.UNKNOWN,
            comment="Aucun besoin cité dans une source : critère non évaluable.",
        )
    if not data.evidence_urls:
        # Un besoin affirmé sans source reste une hypothèse interne.
        return CriterionScore(
            key="documented_need",
            label="Besoin documenté",
            points=round(maximum * 0.4),
            max_points=maximum,
            status=VerificationStatus.HYPOTHESIS,
            comment="Besoin évoqué mais sans source citée.",
        )
    points = maximum if len(quotes) >= 2 else round(maximum * 0.8)
    return CriterionScore(
        key="documented_need",
        label="Besoin documenté",
        points=points,
        max_points=maximum,
        status=VerificationStatus.VERIFIED,
        evidence=data.evidence_urls[:3],
        comment=f"{len(quotes)} signal(aux) de besoin cité(s) depuis des sources datées.",
    )


def _score_decision_maker(data: ScoringInput) -> CriterionScore:
    maximum = CRITERION_WEIGHTS["reachable_decision_maker"]
    email = normalize_email(data.contact_email)
    role = strip_accents(data.contact_role or "").lower()

    if not data.contact_full_name and not email:
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=0,
            max_points=maximum,
            status=VerificationStatus.UNKNOWN,
            comment="Aucun contact identifié.",
        )
    if not email:
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=round(maximum * 0.3),
            max_points=maximum,
            status=VerificationStatus.HYPOTHESIS,
            comment="Contact nommé mais sans adresse professionnelle vérifiée.",
        )

    contact_domain = email_domain(email)
    org_domain = normalize_domain(data.domain) or normalize_domain(data.website)
    domain_matches = bool(contact_domain and org_domain and contact_domain == org_domain)

    if is_public_email_domain(contact_domain):
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=round(maximum * 0.25),
            max_points=maximum,
            status=VerificationStatus.HYPOTHESIS,
            comment=(
                "Adresse de messagerie grand public : rattachement à l'organisation non démontré."
            ),
        )

    keywords = DECISION_MAKER_KEYWORDS.get(data.product_code.lower(), ())
    role_matches = any(keyword in role for keyword in keywords)
    is_executive = any(keyword in role for keyword in EXECUTIVE_KEYWORDS)

    if domain_matches and role_matches:
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=maximum,
            max_points=maximum,
            status=VerificationStatus.VERIFIED,
            evidence=data.evidence_urls[:2],
            comment=(
                f"Fonction « {data.contact_role} » pertinente, adresse au "
                "domaine de l'organisation."
            ),
        )
    if domain_matches and is_executive:
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=round(maximum * 0.75),
            max_points=maximum,
            status=VerificationStatus.VERIFIED,
            evidence=data.evidence_urls[:2],
            comment="Fonction de direction, mais pas directement en charge du périmètre visé.",
        )
    if domain_matches:
        return CriterionScore(
            key="reachable_decision_maker",
            label="Décideur accessible",
            points=round(maximum * 0.5),
            max_points=maximum,
            status=VerificationStatus.HYPOTHESIS,
            comment="Contact au bon domaine, pouvoir de décision non établi.",
        )
    return CriterionScore(
        key="reachable_decision_maker",
        label="Décideur accessible",
        points=round(maximum * 0.25),
        max_points=maximum,
        status=VerificationStatus.HYPOTHESIS,
        comment="Adresse professionnelle sans lien démontré avec l'organisation.",
    )


def _score_pilot_feasibility(data: ScoringInput) -> CriterionScore:
    maximum = CRITERION_WEIGHTS["pilot_feasibility"]
    reasons: list[str] = []
    points = 0

    if data.evidence_urls:
        points += round(maximum * 0.4)
        reasons.append("provenance documentée")
    if normalize_email(data.contact_email):
        points += round(maximum * 0.4)
        reasons.append("canal de contact disponible")
    if data.headcount_estimate:
        points += round(maximum * 0.2)
        reasons.append("taille connue")

    if points == 0:
        return CriterionScore(
            key="pilot_feasibility",
            label="Faisabilité du pilote",
            points=0,
            max_points=maximum,
            status=VerificationStatus.UNKNOWN,
            comment="Ni source, ni contact, ni taille : pilote non planifiable.",
        )

    status = (
        VerificationStatus.VERIFIED
        if points >= round(maximum * 0.8)
        else VerificationStatus.HYPOTHESIS
    )
    return CriterionScore(
        key="pilot_feasibility",
        label="Faisabilité du pilote",
        points=min(points, maximum),
        max_points=maximum,
        status=status,
        evidence=data.evidence_urls[:1],
        comment="Éléments réunis : " + ", ".join(reasons) + ".",
    )


def _score_potential(data: ScoringInput) -> CriterionScore:
    maximum = CRITERION_WEIGHTS["potential"]
    headcount = data.headcount_estimate
    if headcount is None:
        return CriterionScore(
            key="potential",
            label="Potentiel",
            points=0,
            max_points=maximum,
            status=VerificationStatus.UNKNOWN,
            comment="Taille de l'organisation inconnue.",
        )

    low = data.min_headcount
    high = data.max_headcount
    if low is not None and headcount < low:
        return CriterionScore(
            key="potential",
            label="Potentiel",
            points=round(maximum * 0.2),
            max_points=maximum,
            status=VerificationStatus.VERIFIED,
            comment=f"{headcount} personnes : sous le seuil de {low} du profil cible.",
        )
    if high is not None and headcount > high:
        return CriterionScore(
            key="potential",
            label="Potentiel",
            points=round(maximum * 0.6),
            max_points=maximum,
            status=VerificationStatus.VERIFIED,
            comment=(
                f"{headcount} personnes : au-dessus de la cible, cycle de "
                "vente probablement plus long."
            ),
        )
    return CriterionScore(
        key="potential",
        label="Potentiel",
        points=maximum,
        max_points=maximum,
        status=VerificationStatus.VERIFIED,
        evidence=data.evidence_urls[:1],
        comment=f"{headcount} personnes : dans la fourchette visée.",
    )


# --- Synthèse ----------------------------------------------------------------


def _is_excluded(data: ScoringInput) -> bool:
    if not data.exclusion_signals:
        return False
    haystack = strip_accents(
        " ".join(
            filter(
                None,
                [data.organization_name, data.sector or "", " ".join(data.need_quotes)],
            )
        )
    ).lower()
    return any(
        strip_accents(signal).lower() in haystack for signal in data.exclusion_signals if signal
    )


def _shares_token(left: str, right: str) -> bool:
    left_tokens = {t for t in left.split() if len(t) > 3}
    right_tokens = {t for t in right.split() if len(t) > 3}
    return bool(left_tokens & right_tokens)


def _confidence_from(total: int, evidence_ratio: float, unknown_count: int) -> str:
    if unknown_count >= 3 or evidence_ratio < 0.25:
        return "low"
    if total >= 70 and evidence_ratio >= 0.6 and unknown_count <= 1:
        return "high"
    return "medium"


def _build_rationale(
    criteria: list[CriterionScore],
    total: int,
    evidence_ratio: float,
    verification: str,
    unknowns: list[str],
) -> str:
    strongest = max(criteria, key=lambda c: c.points)
    parts = [
        f"Score {total}/{MAX_SCORE}.",
        f"Critère le mieux noté : {strongest.label} ({strongest.points}/{strongest.max_points}).",
        f"{round(evidence_ratio * 100)} % des points reposent sur des sources datées.",
    ]
    if unknowns:
        parts.append("Inconnues : " + ", ".join(unknowns) + ".")
    if verification == VerificationStatus.REJECTED:
        parts.append("Prospect écarté : signal d'exclusion du profil cible détecté.")
    elif verification != VerificationStatus.VERIFIED:
        parts.append(
            "Statut non vérifié : la prospection reste bloquée tant que les sources manquent."
        )
    return " ".join(parts)
