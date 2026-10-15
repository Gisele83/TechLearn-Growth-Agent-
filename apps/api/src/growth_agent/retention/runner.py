"""Exécution planifiée : verrou, battement de cœur, reprise après incident.

Le verrou est porté par la table `purge_runs` plutôt que par un mécanisme
externe : il survit ainsi à un redémarrage du processus, et son état est
consultable en SQL quand on cherche à comprendre pourquoi rien ne s'est
exécuté cette nuit.

**Reprise.** La purge est idempotente : un prospect anonymisé ne correspond
plus aux critères de sélection, un événement supprimé non plus. Une exécution
interrompue ne laisse donc rien d'incohérent — la suivante reprend simplement
le reliquat. C'est pour cela que les lots sont validés séparément : le travail
déjà fait est acquis.

**Détection d'interruption.** Une exécution `running` dont le battement de
cœur date de plus de `purge_lock_timeout_minutes` est considérée comme morte
(processus tué, machine redémarrée). Elle est marquée `interrupted` et son
verrou est repris, en conservant la trace de ce qu'elle avait accompli.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import Settings, get_settings
from growth_agent.models.ops import PurgeRun
from growth_agent.retention.policy import default_now
from growth_agent.retention.service import PurgeReport, PurgeService, SafetyStop
from growth_agent.security.redaction import redact_text

logger = logging.getLogger("growth_agent.retention")


class PurgeLockBusy(Exception):
    """Une autre exécution est en cours."""


def reclaim_stale_runs(session: Session, settings: Settings | None = None) -> list[PurgeRun]:
    """Marque `interrupted` les exécutions dont le battement de cœur est figé."""
    settings = settings or get_settings()
    threshold = default_now() - timedelta(minutes=settings.purge_lock_timeout_minutes)

    stale = list(
        session.execute(
            select(PurgeRun).where(
                PurgeRun.status == "running",
                PurgeRun.heartbeat_at < threshold,
            )
        ).scalars()
    )
    for run in stale:
        run.status = "interrupted"
        run.finished_at = default_now()
        run.stopped_reason = (
            "Battement de cœur figé au-delà du délai : exécution considérée comme "
            "interrompue. Le travail déjà validé est conservé ; le reliquat sera "
            "repris à la prochaine exécution."
        )
        logger.warning("Exécution de purge %s marquée interrompue (battement figé).", run.id)
    if stale:
        session.flush()
    return stale


def active_run(session: Session, settings: Settings | None = None) -> PurgeRun | None:
    """Exécution réellement en cours, après reprise des verrous morts."""
    reclaim_stale_runs(session, settings)
    return (
        session.execute(
            select(PurgeRun)
            .where(PurgeRun.status == "running")
            .order_by(PurgeRun.started_at.desc())
        )
        .scalars()
        .first()
    )


def run_purge(
    session: Session,
    *,
    dry_run: bool | None = None,
    actor: str = "scheduler",
    settings: Settings | None = None,
) -> tuple[PurgeRun, PurgeReport]:
    """Exécute une purge sous verrou, en enregistrant son déroulement.

    Une simulation ne prend pas le verrou : elle n'écrit rien, et devoir
    attendre la fin d'une purge réelle pour savoir ce qu'elle ferait n'aurait
    pas de sens.
    """
    settings = settings or get_settings()
    effective_dry_run = settings.purge_dry_run_default if dry_run is None else dry_run

    if effective_dry_run:
        service = PurgeService(session, settings=settings)
        report = service.apply(dry_run=True, actor=actor)
        run = PurgeRun(
            # Une simulation dont le plan est bloqué rapporte « bloqué » : elle
            # annonce ce que ferait une exécution réelle, refus compris.
            status="blocked" if report.plan and report.plan.blocked else "succeeded",
            dry_run=True,
            triggered_by=actor,
            started_at=report.started_at,
            finished_at=report.finished_at,
            heartbeat_at=report.finished_at or report.started_at,
            counts=dict(report.counts),
            stopped_reason=report.stopped_reason,
        )
        session.add(run)
        session.flush()
        return run, report

    previous = active_run(session, settings)
    if previous is not None:
        raise PurgeLockBusy(
            f"Une purge est déjà en cours depuis {previous.started_at.isoformat()} "
            f"(exécution {previous.id})."
        )

    # Si la précédente exécution a été interrompue, on le consigne : c'est
    # ainsi qu'on sait, en lisant la table, qu'il y a eu reprise.
    last_interrupted = (
        session.execute(
            select(PurgeRun)
            .where(PurgeRun.status == "interrupted")
            .order_by(PurgeRun.started_at.desc())
        )
        .scalars()
        .first()
    )

    started = default_now()
    run = PurgeRun(
        status="running",
        dry_run=False,
        triggered_by=actor,
        started_at=started,
        heartbeat_at=started,
        counts={},
        resumed_from_id=last_interrupted.id if last_interrupted else None,
    )
    session.add(run)
    # Validation immédiate : le verrou doit être visible des autres processus
    # avant que le travail ne commence.
    session.commit()

    def _on_batch(counts: dict[str, int]) -> None:
        run.counts = {**run.counts, **counts}
        run.heartbeat_at = default_now()
        # Chaque lot est acquis : une interruption après ce point ne perd rien.
        session.commit()

    try:
        service = PurgeService(session, settings=settings)
        report = service.apply(dry_run=False, actor=actor, now=started, on_batch=_on_batch)
    except SafetyStop as exc:
        run.status = "blocked"
        run.stopped_reason = str(exc)
        run.finished_at = default_now()
        session.commit()
        raise
    except Exception as exc:
        run.status = "failed"
        # Une erreur SQLAlchemy porte l'instruction ET ses paramètres : une
        # adresse email peut s'y trouver. On masque avant de stocker, et on
        # tronque ensuite — dans cet ordre, sinon la troncature pourrait
        # couper un motif au milieu et le rendre méconnaissable du masquage.
        run.error_message = redact_text(f"{type(exc).__name__}: {exc}")[:500]
        run.finished_at = default_now()
        session.commit()
        logger.exception("Échec de la purge %s", run.id)
        raise

    run.status = "blocked" if report.plan and report.plan.blocked else "succeeded"
    run.counts = dict(report.counts)
    run.stopped_reason = report.stopped_reason
    run.finished_at = report.finished_at or default_now()
    run.heartbeat_at = run.finished_at
    session.commit()

    logger.info("Purge %s terminée : %s enregistrement(s) traité(s).", run.id, report.total)
    return run, report
