"""Mesures d'exploitation et seuils d'alerte.

Deux principes ont guidé le choix des mesures.

**Aucune donnée personnelle, aucune cardinalité non bornée.** Les métriques
sont lues par un système tiers et conservées longtemps ; une étiquette portant
une adresse ou un identifiant de prospect y laisserait des données
personnelles hors de toute politique de rétention. Les seules étiquettes
utilisées sont le code produit, le mode d'envoi et le motif de blocage —
trois ensembles fermés.

**Ce qu'on surveille, c'est ce qui fait mal.** Pas le nombre de requêtes par
seconde, qui n'apprend rien sur une application commerciale à faible trafic,
mais : un envoi dont on ignore s'il est parti, une réservation abandonnée par
un processus mort, une purge qui n'a pas tourné, une sauvegarde qui date, et
surtout l'envoi réel activé sans qu'on l'ait décidé.

Les seuils sont définis **une seule fois**, ici. L'endpoint d'alerte et le
fichier de règles Prometheus en dépendent tous les deux, et un test vérifie
qu'ils ne divergent pas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent import __version__
from growth_agent.config import Settings
from growth_agent.models.catalog import Product
from growth_agent.models.enums import ApprovalDecision, DeliveryState, OutreachEventType
from growth_agent.models.ops import PurgeRun
from growth_agent.models.outreach import ApprovalRequest, OutreachEvent, SuppressionEntry
from growth_agent.retention.runner import (
    PURGE_BLOCKED,
    PURGE_FAILED,
    PURGE_INTERRUPTED,
    PURGE_SUCCEEDED,
)
from growth_agent.services.inbox_relay import check_freshness

#: Au-delà de ce délai, une réservation d'envoi encore à l'état « reserved »
#: ne correspond plus à un envoi en cours mais à un processus mort.
STALE_RESERVATION_MINUTES = 15


@dataclass(frozen=True)
class AlertRule:
    """Un seuil, son motif, et ce qu'il faut faire quand il se déclenche.

    Le `runbook` n'est pas décoratif : une alerte sans geste associé réveille
    quelqu'un qui ne saura pas quoi faire, et finit par être ignorée.
    """

    name: str
    metric: str
    threshold: float
    #: "gt" : alerte si la valeur dépasse le seuil. "lt" : si elle passe dessous.
    comparison: str
    severity: str
    summary: str
    runbook: str

    def fires(self, value: float | None) -> bool:
        if value is None:
            return False
        return value > self.threshold if self.comparison == "gt" else value < self.threshold


#: Les seuils. Toute modification doit être répercutée dans
#: `monitoring/alerts.rules.yml` — un test échoue sinon.
ALERT_RULES: tuple[AlertRule, ...] = (
    AlertRule(
        name="EnvoiReelActiveSansAutorisation",
        metric="growth_agent_email_sending_enabled",
        threshold=0,
        comparison="gt",
        severity="critical",
        summary="L'envoi réel d'emails est activé.",
        runbook=(
            "Vérifier qu'un procès-verbal de mise en service autorise l'envoi réel "
            "(domaine, SPF/DKIM/DMARC, cadre juridique). Sinon, repasser "
            "GA_EMAIL_SENDING_ENABLED à false immédiatement."
        ),
    ),
    AlertRule(
        name="EnvoisIssueInconnue",
        metric="growth_agent_deliveries_uncertain",
        threshold=0,
        comparison="gt",
        severity="critical",
        summary="Des envois se sont terminés sans réponse lisible du fournisseur.",
        runbook=(
            "Ces messages sont peut-être partis. Ils ne seront jamais rejoués "
            "automatiquement. Consulter le journal du fournisseur, puis trancher "
            "message par message. Ne pas relancer en masse."
        ),
    ),
    AlertRule(
        name="ReservationsAbandonnees",
        metric="growth_agent_reservations_stale",
        threshold=0,
        comparison="gt",
        severity="warning",
        summary="Des réservations d'envoi sont restées en attente.",
        runbook=(
            f"Une réservation de plus de {STALE_RESERVATION_MINUTES} minutes "
            "signale un processus mort entre la réservation et l'envoi. Le "
            "message correspondant est bloqué tant qu'un humain n'a pas tranché."
        ),
    ),
    AlertRule(
        name="EnvoisBloquesEnHausse",
        metric="growth_agent_sends_blocked_24h",
        threshold=20,
        comparison="gt",
        severity="warning",
        summary="Beaucoup d'envois bloqués sur 24 heures.",
        runbook=(
            "Une hausse soudaine signale un problème de données ou de "
            "configuration, pas une série de refus légitimes. Regarder la "
            "répartition par motif dans /api/v1/system/alerts avant toute action."
        ),
    ),
    AlertRule(
        name="PurgeEnRetard",
        metric="growth_agent_purge_last_success_age_seconds",
        threshold=172800,  # 48 heures
        comparison="gt",
        severity="warning",
        summary="Aucune purge réussie depuis plus de 48 heures.",
        runbook=(
            "La purge est quotidienne. Deux jours sans succès signifie que des "
            "données dépassent leur durée de conservation. Vérifier la tâche "
            "planifiée et /api/v1/system/retention/runs."
        ),
    ),
    AlertRule(
        name="PurgeEnEchec",
        metric="growth_agent_purge_failures_24h",
        threshold=0,
        comparison="gt",
        severity="warning",
        summary="Une purge a échoué ou a été interrompue dans les 24 dernières heures.",
        runbook=(
            "Lire `error_message` dans /api/v1/system/retention/runs. La purge "
            "étant idempotente, la relancer est sans danger : elle repart des "
            "enregistrements encore échus."
        ),
    ),
    AlertRule(
        name="PurgeArreteeParGardeFou",
        metric="growth_agent_purge_blocked_24h",
        threshold=0,
        comparison="gt",
        severity="warning",
        summary="Une purge a été arrêtée par un garde-fou.",
        runbook=(
            "Ce n'est pas une panne : le volume à supprimer dépassait le "
            "plafond, et le garde-fou a fait son travail. Lire `stopped_reason` "
            "dans /api/v1/system/retention/runs et vérifier d'où vient le "
            "volume avant de relever le plafond. Ne jamais le relever pour "
            "faire passer une purge qu'on n'a pas expliquée."
        ),
    ),
    AlertRule(
        name="BoiteDeReponseNonRelevee",
        metric="growth_agent_inbox_last_declared_relay_age_seconds",
        # 96 heures : un relevé le vendredi matin et le suivant le lundi
        # matin laissent 72 heures tout à fait normales. Alerter à 72 ferait
        # sonner chaque week-end, et une alerte qui sonne pour rien finit
        # par ne plus être lue.
        threshold=345600,
        comparison="gt",
        severity="critical",
        summary=(
            "Aucun relevé de la boîte de réponse déclaré depuis quatre jours. "
            "Les relances sont suspendues."
        ),
        runbook=(
            "Chaque message promet « répondez STOP ». Cette promesse repose sur "
            "un relevé quotidien déclaré, et il s'est interrompu : une "
            "opposition peut dormir dans la boîte. Les relances sont déjà "
            "suspendues automatiquement ; les oppositions déjà enregistrées, "
            "elles, restent traitées. Relever contact@techlearn-saem.com, puis "
            "déclarer le relevé (POST /api/v1/replies/relay-check). "
            "Destinataires à prévenir : /api/v1/system/alerts, champ « notify ». "
            "Si personne ne peut s'en charger durablement, vider "
            "GA_REPLY_TO_EMAIL — les messages cesseront d'inviter à répondre."
        ),
    ),
    AlertRule(
        name="SauvegardeEnRetard",
        metric="growth_agent_backup_last_success_age_seconds",
        threshold=172800,  # 48 heures
        comparison="gt",
        severity="critical",
        summary="Aucune sauvegarde réussie depuis plus de 48 heures.",
        runbook=(
            "Vérifier la tâche de sauvegarde et l'espace disque de destination. "
            "Une sauvegarde jamais restaurée n'étant pas une sauvegarde, "
            "profiter de l'incident pour rejouer une restauration d'essai."
        ),
    ),
    AlertRule(
        name="BaseInjoignable",
        metric="growth_agent_database_up",
        threshold=1,
        comparison="lt",
        severity="critical",
        summary="La base de données ne répond pas.",
        runbook=(
            "La sonde de disponibilité renvoie 503 et le trafic doit être "
            "retiré. Ne pas redémarrer l'application : elle n'est pas en cause."
        ),
    ),
    AlertRule(
        name="ConfigurationIncoherente",
        metric="growth_agent_configuration_warnings",
        threshold=0,
        comparison="gt",
        severity="warning",
        summary="La configuration comporte des avertissements.",
        runbook="Détail dans /health. Corriger avant toute opération commerciale.",
    ),
)


@dataclass
class Sample:
    """Une mesure, avec ses étiquettes."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    help_text: str = ""
    kind: str = "gauge"


def backup_age_seconds(settings: Settings, *, now: datetime | None = None) -> float | None:
    """Âge de la dernière sauvegarde réussie, d'après un fichier témoin.

    La tâche de sauvegarde touche ce fichier **après** avoir vérifié que le
    transfert s'est bien passé. Le choix d'un fichier plutôt que d'une table
    est délibéré : une sauvegarde doit pouvoir être constatée même quand la
    base est en panne, c'est-à-dire précisément quand elle compte.

    `None` quand aucun chemin n'est configuré — l'absence de mesure n'est pas
    un retard de sauvegarde, et ne doit pas déclencher d'alerte.
    """
    if not settings.backup_stamp_path:
        return None
    try:
        modified = os.path.getmtime(settings.backup_stamp_path)
    except OSError:
        # Chemin configuré mais fichier absent : aucune sauvegarde n'a jamais
        # abouti. C'est une alerte, pas une absence de mesure.
        return float("inf")
    moment = now or datetime.now(UTC)
    return max(0.0, moment.timestamp() - modified)


def collect(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> list[Sample]:
    """Relève l'état courant. Lecture seule, sans effet de bord."""
    moment = now or datetime.now(UTC)
    day_start = moment - timedelta(hours=24)
    samples: list[Sample] = []

    try:
        session.execute(select(func.count()).select_from(Product)).scalar_one()
        database_up = 1.0
    except Exception:
        # Sans base, aucune mesure métier n'est disponible — mais la mesure
        # qui dit « la base est tombée » doit sortir quand même.
        return [
            Sample(
                "growth_agent_database_up",
                0.0,
                help_text="1 si la base répond, 0 sinon.",
            )
        ]

    samples.append(
        Sample("growth_agent_database_up", database_up, help_text="1 si la base répond, 0 sinon.")
    )
    samples.append(
        Sample(
            "growth_agent_info",
            1.0,
            labels={"version": __version__, "environment": str(settings.environment)},
            help_text="Version et environnement de l'instance.",
        )
    )
    samples.append(
        Sample(
            "growth_agent_email_sending_enabled",
            1.0 if settings.can_send_email() else 0.0,
            help_text="1 si un message peut réellement quitter la machine.",
        )
    )
    samples.append(
        Sample(
            "growth_agent_configuration_warnings",
            float(len(settings.configuration_warnings())),
            help_text="Nombre d'avertissements de configuration.",
        )
    )

    products = {product.id: product.code for product in session.execute(select(Product)).scalars()}

    # --- Envois, séparés par mode -----------------------------------------
    rows = session.execute(
        select(
            OutreachEvent.product_id,
            OutreachEvent.is_simulated,
            func.count(),
        )
        .where(
            OutreachEvent.event_type == OutreachEventType.SENT,
            OutreachEvent.delivery_state == DeliveryState.SENT,
            OutreachEvent.occurred_at >= day_start,
        )
        .group_by(OutreachEvent.product_id, OutreachEvent.is_simulated)
    ).all()
    sends = {
        (products.get(product_id, "inconnu"), "simule" if simulated else "reel"): int(total)
        for product_id, simulated, total in rows
    }
    for code in list(products.values()) or ["inconnu"]:
        for mode in ("reel", "simule"):
            samples.append(
                Sample(
                    "growth_agent_sends_24h",
                    float(sends.get((code, mode), 0)),
                    labels={"product": code, "mode": mode},
                    help_text="Messages remis sur 24 heures, par mode d'envoi.",
                )
            )

    # --- Blocages, par motif ----------------------------------------------
    blocked_rows = session.execute(
        select(OutreachEvent.payload, func.count())
        .where(
            OutreachEvent.event_type == OutreachEventType.SEND_BLOCKED,
            OutreachEvent.occurred_at >= day_start,
        )
        .group_by(OutreachEvent.payload)
    ).all()
    by_reason: dict[str, int] = {}
    for payload, total in blocked_rows:
        reason = (payload or {}).get("reason_code", "inconnu")
        by_reason[str(reason)] = by_reason.get(str(reason), 0) + int(total)
    for reason, total in sorted(by_reason.items()):
        samples.append(
            Sample(
                "growth_agent_sends_blocked_24h",
                float(total),
                labels={"reason": reason},
                help_text="Envois refusés par un garde-fou sur 24 heures, par motif.",
            )
        )
    if not by_reason:
        samples.append(
            Sample(
                "growth_agent_sends_blocked_24h",
                0.0,
                labels={"reason": "aucun"},
                help_text="Envois refusés par un garde-fou sur 24 heures, par motif.",
            )
        )

    # --- Envois à arbitrer --------------------------------------------------
    uncertain = session.execute(
        select(func.count())
        .select_from(OutreachEvent)
        .where(OutreachEvent.delivery_state == DeliveryState.UNCERTAIN)
    ).scalar_one()
    samples.append(
        Sample(
            "growth_agent_deliveries_uncertain",
            float(uncertain),
            help_text="Envois d'issue inconnue, en attente d'arbitrage humain. Jamais rejoués.",
        )
    )

    stale_before = moment - timedelta(minutes=STALE_RESERVATION_MINUTES)
    stale = session.execute(
        select(func.count())
        .select_from(OutreachEvent)
        .where(
            OutreachEvent.delivery_state == DeliveryState.RESERVED,
            OutreachEvent.occurred_at < stale_before,
        )
    ).scalar_one()
    samples.append(
        Sample(
            "growth_agent_reservations_stale",
            float(stale),
            help_text=(
                f"Réservations d'envoi de plus de {STALE_RESERVATION_MINUTES} minutes : "
                "un processus est mort entre la réservation et l'envoi."
            ),
        )
    )

    # --- File d'attente humaine --------------------------------------------
    pending = session.execute(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(ApprovalRequest.decision == ApprovalDecision.PENDING)
    ).scalar_one()
    samples.append(
        Sample(
            "growth_agent_approvals_pending",
            float(pending),
            help_text="Demandes de validation en attente d'une décision humaine.",
        )
    )

    samples.append(
        Sample(
            "growth_agent_suppression_entries",
            float(session.execute(select(func.count()).select_from(SuppressionEntry)).scalar_one()),
            help_text="Oppositions à la prospection enregistrées.",
        )
    )

    # --- Purge ---------------------------------------------------------------
    # Les statuts viennent du runner, importés plutôt que recopiés. Ils
    # avaient été recopiés, et faux : la métrique ne sortait jamais et
    # l'alerte « purge en retard » n'aurait jamais pu se déclencher.
    last_success = session.execute(
        select(func.max(PurgeRun.finished_at)).where(
            PurgeRun.status == PURGE_SUCCEEDED, PurgeRun.dry_run.is_(False)
        )
    ).scalar_one_or_none()
    if last_success is not None:
        samples.append(
            Sample(
                "growth_agent_purge_last_success_age_seconds",
                max(0.0, (moment - _aware(last_success)).total_seconds()),
                help_text="Temps écoulé depuis la dernière purge réelle réussie.",
            )
        )

    failures = session.execute(
        select(func.count())
        .select_from(PurgeRun)
        .where(
            PurgeRun.status.in_((PURGE_FAILED, PURGE_INTERRUPTED)),
            PurgeRun.started_at >= day_start,
        )
    ).scalar_one()
    samples.append(
        Sample(
            "growth_agent_purge_failures_24h",
            float(failures),
            help_text="Purges en échec ou interrompues sur 24 heures.",
        )
    )

    # Un arrêt par garde-fou n'est pas une panne : c'est le garde-fou qui fait
    # son travail devant un volume anormal. Il demande un examen humain, pas
    # un dépannage — d'où une mesure et une alerte distinctes.
    blocked = session.execute(
        select(func.count())
        .select_from(PurgeRun)
        .where(PurgeRun.status == PURGE_BLOCKED, PurgeRun.started_at >= day_start)
    ).scalar_one()
    samples.append(
        Sample(
            "growth_agent_purge_blocked_24h",
            float(blocked),
            help_text="Purges arrêtées par un garde-fou sur 24 heures.",
        )
    )

    # --- Relevé déclaré de la boîte de réponse -------------------------------
    # Mesuré seulement quand les messages invitent à répondre : sans cette
    # invitation, personne n'a rien promis et il n'y a rien à surveiller.
    #
    # « declared » est dans le nom, et ce n'est pas une précaution de style :
    # cette mesure suit une déclaration nominative, pas une consultation
    # constatée. Aucune preuve technique de lecture n'est accessible d'ici.
    freshness = check_freshness(session, settings=settings, now=moment)
    if freshness.applicable:
        samples.append(
            Sample(
                "growth_agent_inbox_last_declared_relay_age_seconds",
                float("inf") if freshness.age_seconds is None else freshness.age_seconds,
                help_text=(
                    "Temps écoulé depuis la dernière DÉCLARATION de relevé de la "
                    "boîte de réponse. Déclaration nominative, pas preuve de "
                    "consultation."
                ),
            )
        )
        samples.append(
            Sample(
                "growth_agent_follow_ups_held_by_stale_inbox",
                0.0 if freshness.fresh else 1.0,
                help_text=(
                    "1 quand les relances sont suspendues faute de relevé déclaré assez récent."
                ),
            )
        )

    # --- Sauvegarde ----------------------------------------------------------
    age = backup_age_seconds(settings, now=moment)
    if age is not None:
        samples.append(
            Sample(
                "growth_agent_backup_last_success_age_seconds",
                age,
                help_text="Temps écoulé depuis la dernière sauvegarde réussie constatée.",
            )
        )

    return samples


def evaluate_alerts(
    samples: list[Sample], *, settings: Settings | None = None
) -> list[dict[str, Any]]:
    """Alertes actuellement déclenchées, d'après les mesures relevées.

    Permet de superviser sans Prometheus : tout le monde n'en a pas un, et une
    alerte qui suppose une pile complète n'est pas une alerte pour tout le
    monde.
    """
    worst: dict[str, float] = {}
    for sample in samples:
        worst[sample.name] = max(worst.get(sample.name, float("-inf")), sample.value)

    firing: list[dict[str, Any]] = []
    for rule in ALERT_RULES:
        value = worst.get(rule.metric)
        if not rule.fires(value):
            continue
        firing.append(
            {
                "name": rule.name,
                "severity": rule.severity,
                "metric": rule.metric,
                # JSON ne sait pas écrire l'infini : sérialisé tel quel, il
                # ressort en `null`, qui se lit « aucune mesure » — soit
                # l'inverse de ce que l'alerte signale. Le texte tranche.
                "value": None if is_infinite(value) else value,
                "value_text": describe_value(value),
                "threshold": rule.threshold,
                "comparison": rule.comparison,
                "summary": rule.summary,
                "runbook": rule.runbook,
                # Jamais dans une étiquette de métrique : une adresse y
                # deviendrait une donnée personnelle conservée par un système
                # tiers, hors de toute politique de rétention. Ici, la réponse
                # est authentifiée et éphémère.
                "notify": _recipients(rule, settings),
            }
        )
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    firing.sort(key=lambda item: severity_order.get(item["severity"], 9))
    return firing


#: Alertes dont les destinataires sont nommés dans la configuration.
_RULE_RECIPIENTS: dict[str, tuple[str, ...]] = {
    "BoiteDeReponseNonRelevee": ("inbox_relay_owner_email", "inbox_relay_deputy_email"),
}


def _recipients(rule: AlertRule, settings: Settings | None) -> list[str]:
    """Qui prévenir. Lu depuis la configuration, jamais depuis une métrique."""
    if settings is None:
        return []
    return [
        address
        for field in _RULE_RECIPIENTS.get(rule.name, ())
        if (address := getattr(settings, field, ""))
    ]


def is_infinite(value: float | None) -> bool:
    return value is not None and value in (float("inf"), float("-inf"))


def describe_value(value: float | None) -> str:
    if value is None:
        return "non mesuré"
    if value == float("inf"):
        return "jamais constaté"
    if value == float("-inf"):
        return "valeur aberrante"
    return _render_value(value)


def render_prometheus(samples: list[Sample]) -> str:
    """Format d'exposition textuel de Prometheus.

    Écrit à la main plutôt qu'avec une bibliothèque : le format tient en
    quelques lignes, et une dépendance de plus dans un connecteur commercial
    est une dépendance à suivre, à mettre à jour et à auditer.
    """
    lines: list[str] = []
    documented: set[str] = set()
    for sample in samples:
        if sample.name not in documented:
            if sample.help_text:
                lines.append(f"# HELP {sample.name} {_escape_help(sample.help_text)}")
            lines.append(f"# TYPE {sample.name} {sample.kind}")
            documented.add(sample.name)
        lines.append(f"{sample.name}{_render_labels(sample.labels)} {_render_value(sample.value)}")
    return "\n".join(lines) + "\n"


def _render_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape_label(value)}"' for key, value in sorted(labels.items()))
    return "{" + inner + "}"


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", " ")


def _render_value(value: float) -> str:
    if value == float("inf"):
        return "+Inf"
    if value == float("-inf"):
        return "-Inf"
    return repr(float(value))


def _aware(moment: datetime) -> datetime:
    """SQLite rend des horodatages naïfs ; PostgreSQL les rend avec fuseau."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)
