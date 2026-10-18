"""Entonnoir par cohorte, délais médians, motifs de perte.

Un entonnoir en volumes cumulés ne dit pas si la conversion s'améliore : il
mélange des organisations contactées en janvier avec d'autres contactées
hier, dont certaines n'ont pas encore eu le temps de répondre. Le total
monte, et on ne sait pas pourquoi.

Lu **par cohorte de départ** — le mois du premier contact réel — chaque
ligne devient comparable à la précédente, et le creux se voit.

Trois précautions, héritées des lots précédents.

**Seuls les envois réels comptent.** Une répétition en simulation n'a atteint
personne ; la compter comme un contact fausserait toute la colonne.

**La médiane, pas la moyenne.** Un dossier qui traîne six mois déplace une
moyenne et ne dit rien du cas courant. La médiane résiste.

**Une cohorte trop jeune est signalée, pas masquée.** Une cohorte du mois
courant aura mécaniquement moins de conversions : afficher son taux sans
avertir inviterait à conclure d'une baisse qui n'est qu'un manque de recul.
"""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.models.enums import (
    DeliveryState,
    EventSource,
    NextActionStatus,
    OpportunityStage,
    OutreachEventType,
)
from growth_agent.models.outreach import OutreachEvent
from growth_agent.models.pipeline import NextAction, Opportunity

#: Étapes du parcours, dans l'ordre, telles qu'elles s'enchaînent réellement.
JOURNEY: tuple[tuple[str, str], ...] = (
    ("contacted", OutreachEventType.SENT),
    ("replied", OutreachEventType.REPLIED),
    ("demo_scheduled", OutreachEventType.DEMO_SCHEDULED),
    ("demo_held", OutreachEventType.DEMO_HELD),
    ("pilot_started", OutreachEventType.PILOT_STARTED),
    ("won", OutreachEventType.DEAL_WON),
)

#: Faits comptés à côté de l'entonnoir : ils ne font pas avancer un prospect,
#: mais sans eux on ne comprend pas pourquoi il n'avance pas.
SIDE_FACTS: tuple[tuple[str, str], ...] = (
    ("demo_no_show", OutreachEventType.DEMO_NO_SHOW),
    ("opted_out", OutreachEventType.OPTED_OUT),
)

#: En deçà, une cohorte est trop récente pour que ses taux veuillent dire
#: quelque chose : les conversions n'ont pas eu le temps de se produire.
MATURITY_DAYS = 30


@dataclass
class CohortRow:
    """Une cohorte de départ, et ce qu'elle est devenue."""

    cohort: str
    counts: dict[str, int] = field(default_factory=dict)
    conversion: dict[str, float | None] = field(default_factory=dict)
    #: Pour chaque étape, combien de ses prospects reposent sur un événement
    #: constaté par le système plutôt que saisi par une personne.
    observed: dict[str, int] = field(default_factory=dict)
    #: Faux quand la cohorte est trop jeune pour être comparée.
    mature: bool = True
    note: str = ""


@dataclass
class CohortReport:
    product_code: str
    computed_at: datetime
    campaign_slug: str = ""
    cohorts: list[CohortRow] = field(default_factory=list)
    #: Totaux par origine, toutes cohortes confondues. Lire « 12 » sans
    #: savoir d'où vient le chiffre, c'est lui prêter une fiabilité qu'il
    #: n'a peut-être pas.
    by_source: dict[str, int] = field(default_factory=dict)
    side_facts: dict[str, int] = field(default_factory=dict)
    median_days: dict[str, float | None] = field(default_factory=dict)
    loss_reasons: dict[str, int] = field(default_factory=dict)
    blocked_by_reason: dict[str, int] = field(default_factory=dict)
    open_pipeline: dict[str, int] = field(default_factory=dict)
    overdue_by_owner: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["computed_at"] = self.computed_at.isoformat()
        return payload


def _month(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}"


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def compute_cohorts(
    session: Session,
    *,
    product_id: uuid.UUID,
    product_code: str = "",
    campaign_id: uuid.UUID | None = None,
    campaign_slug: str = "",
    months: int = 6,
    now: datetime | None = None,
    include_simulated: bool = False,
) -> CohortReport:
    moment = now or datetime.now(UTC)
    report = CohortReport(
        product_code=product_code, campaign_slug=campaign_slug, computed_at=moment
    )

    # --- Première occurrence de chaque étape, par prospect ----------------
    conditions = [
        OutreachEvent.product_id == product_id,
        OutreachEvent.prospect_id.is_not(None),
    ]
    if not include_simulated:
        conditions.append(OutreachEvent.is_simulated.is_(False))
    if campaign_id is not None:
        conditions.append(OutreachEvent.campaign_id == campaign_id)

    premieres: dict[uuid.UUID, dict[str, datetime]] = defaultdict(dict)
    #: Origine de la première occurrence de chaque étape, par prospect.
    origines: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    rows = session.execute(
        select(
            OutreachEvent.prospect_id,
            OutreachEvent.event_type,
            OutreachEvent.occurred_at,
            OutreachEvent.delivery_state,
            OutreachEvent.event_source,
        ).where(*conditions)
    ).all()
    etapes = {event: nom for nom, event in JOURNEY}
    faits = {event: nom for nom, event in SIDE_FACTS}
    for prospect_id, event_type, occurred_at, delivery_state, event_source in rows:
        if event_type in faits:
            nom_fait = faits[event_type]
            report.side_facts[nom_fait] = report.side_facts.get(nom_fait, 0) + 1
            report.by_source[event_source] = report.by_source.get(event_source, 0) + 1
            continue
        nom = etapes.get(event_type)
        if nom is None:
            continue
        # Une réservation sans suite n'est pas un envoi : seul `sent` compte.
        if event_type == OutreachEventType.SENT and delivery_state not in (
            None,
            DeliveryState.SENT,
        ):
            continue
        report.by_source[event_source] = report.by_source.get(event_source, 0) + 1
        quand = _aware(occurred_at)
        connu = premieres[prospect_id].get(nom)
        if connu is None or quand < connu:
            premieres[prospect_id][nom] = quand
            origines[prospect_id][nom] = event_source

    # --- Cohortes ---------------------------------------------------------
    limite = moment - timedelta(days=31 * max(1, months))
    par_cohorte: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    observes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for prospect_id, etapes_du_prospect in premieres.items():
        depart = etapes_du_prospect.get("contacted")
        # Sans premier contact réel, un prospect n'appartient à aucune
        # cohorte : il n'est pas encore entré dans l'entonnoir.
        if depart is None or depart < limite:
            continue
        cohorte = _month(depart)
        for nom, _ in JOURNEY:
            if nom in etapes_du_prospect:
                par_cohorte[cohorte][nom] += 1
                if origines[prospect_id].get(nom) == EventSource.OBSERVED:
                    observes[cohorte][nom] += 1

    for cohorte in sorted(par_cohorte, reverse=True):
        comptes = dict(par_cohorte[cohorte])
        base = comptes.get("contacted", 0)
        ligne = CohortRow(
            cohort=cohorte,
            counts={nom: comptes.get(nom, 0) for nom, _ in JOURNEY},
            conversion={
                nom: (round(comptes.get(nom, 0) / base, 4) if base else None)
                for nom, _ in JOURNEY
                if nom != "contacted"
            },
            observed={nom: observes[cohorte].get(nom, 0) for nom, _ in JOURNEY},
        )
        debut_cohorte = datetime(int(cohorte[:4]), int(cohorte[5:]), 1, tzinfo=UTC)
        if (moment - debut_cohorte).days < MATURITY_DAYS:
            ligne.mature = False
            ligne.note = (
                "Cohorte de moins de 30 jours : les conversions n'ont pas eu le "
                "temps de se produire. Ses taux ne se comparent pas aux autres."
            )
        report.cohorts.append(ligne)

    # --- Délais médians entre étapes --------------------------------------
    for (depuis, _), (vers, _) in zip(JOURNEY, JOURNEY[1:], strict=False):
        ecarts = [
            (etapes_du_prospect[vers] - etapes_du_prospect[depuis]).total_seconds() / 86400
            for etapes_du_prospect in premieres.values()
            if depuis in etapes_du_prospect and vers in etapes_du_prospect
        ]
        report.median_days[f"{depuis}_to_{vers}"] = (
            round(statistics.median(ecarts), 2) if ecarts else None
        )

    # --- Motifs de perte ---------------------------------------------------
    for motif, total in session.execute(
        select(Opportunity.loss_reason, func.count())
        .where(
            Opportunity.product_id == product_id,
            Opportunity.stage == OpportunityStage.LOST,
        )
        .group_by(Opportunity.loss_reason)
    ).all():
        report.loss_reasons[motif or "non_renseigne"] = int(total)

    # --- Dossiers ouverts, par étape --------------------------------------
    for etape, total in session.execute(
        select(Opportunity.stage, func.count())
        .where(Opportunity.product_id == product_id)
        .group_by(Opportunity.stage)
    ).all():
        report.open_pipeline[etape] = int(total)

    # --- Envois bloqués, par motif ----------------------------------------
    bloques = session.execute(
        select(OutreachEvent.payload).where(
            OutreachEvent.product_id == product_id,
            OutreachEvent.event_type == OutreachEventType.SEND_BLOCKED,
            OutreachEvent.occurred_at >= limite,
        )
    ).scalars()
    for payload in bloques:
        motif = str((payload or {}).get("reason_code", "inconnu"))
        report.blocked_by_reason[motif] = report.blocked_by_reason.get(motif, 0) + 1

    # --- Actions en retard, par personne ----------------------------------
    en_retard: dict[str, int] = defaultdict(int)
    for action in session.execute(
        select(NextAction).where(
            NextAction.product_id == product_id,
            NextAction.status == NextActionStatus.OPEN,
            NextAction.due_at < moment,
        )
    ).scalars():
        en_retard[action.owner_email] += 1
    report.overdue_by_owner = [
        {"owner_email": porteur, "overdue": total}
        for porteur, total in sorted(en_retard.items(), key=lambda item: -item[1])
    ]

    # --- Ce qu'il faut savoir en lisant ces chiffres ----------------------
    if not report.cohorts:
        report.notes.append(
            "Aucun envoi réel sur la période : il n'existe encore aucune cohorte. "
            "Les répétitions en simulation ne forment pas de cohorte."
        )
    jeunes = [ligne.cohort for ligne in report.cohorts if not ligne.mature]
    if jeunes:
        report.notes.append(
            f"Cohorte(s) de moins de 30 jours : {', '.join(jeunes)}. "
            "Leurs taux sont mécaniquement bas et ne se comparent pas aux autres."
        )
    declares = report.by_source.get(EventSource.DECLARED, 0)
    total_evenements = sum(report.by_source.values())
    if declares and total_evenements:
        part = round(100 * declares / total_evenements)
        report.notes.append(
            f"{part} % des événements de ce rapport ont été saisis par une "
            "personne, pas constatés par le système. Leur absence ne prouve "
            "rien : une démonstration non saisie reste invisible."
        )
    if report.side_facts.get("demo_no_show"):
        report.notes.append(
            f"{report.side_facts['demo_no_show']} rendez-vous manqué(s) : "
            "comptés à part, ils n'entrent ni dans les démonstrations tenues "
            "ni dans les absences de démonstration."
        )
    if report.loss_reasons.get("non_renseigne"):
        report.notes.append(
            f"{report.loss_reasons['non_renseigne']} dossier(s) perdu(s) sans motif : "
            "ces pertes n'apprennent rien et ne permettent aucune correction."
        )
    return report
