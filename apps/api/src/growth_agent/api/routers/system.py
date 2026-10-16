"""Supervision : sondes, métriques, alertes et état des connecteurs."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import text

from growth_agent import __version__
from growth_agent.api.deps import SessionDep, SettingsDep, require
from growth_agent.db.migrations import revision_state
from growth_agent.schemas.auth import CurrentUser
from growth_agent.schemas.common import HealthResponse
from growth_agent.security.rbac import CAN_ADMINISTER, CAN_READ
from growth_agent.services.observability import (
    collect,
    describe_value,
    evaluate_alerts,
    is_infinite,
    render_prometheus,
)

#: Sonde publique, montée à la racine : un répartiteur de charge ou un
#: conteneur doit pouvoir l'interroger sans jeton ni préfixe de version.
router = APIRouter(tags=["système"])

#: Endpoints de supervision authentifiés, montés sous /api/v1.
admin_router = APIRouter(prefix="/system", tags=["système"])


@router.get("/health/live", include_in_schema=True)
def liveness() -> dict[str, str]:
    """Sonde de **vivacité** : le processus répond-il ?

    Elle n'interroge aucune dépendance, volontairement. Un orchestrateur qui
    redémarre l'application parce que la base est tombée ne répare rien : il
    ajoute une rafale de redémarrages à une panne qui ne le concerne pas, et
    perd au passage les connexions encore valides.
    """
    return {"status": "alive"}


@router.get("/health/ready", include_in_schema=True)
def readiness(session: SessionDep, settings: SettingsDep, response: Response) -> dict[str, object]:
    """Sonde de **disponibilité** : cette instance peut-elle servir du trafic ?

    Renvoie 503 dès qu'une condition manque, pour que le répartiteur de charge
    la retire sans que personne n'ait à intervenir. Trois conditions :
    la base répond, le schéma est à la révision attendue, et la configuration
    ne comporte pas d'incohérence bloquante.

    Le schéma en fait partie parce qu'un conteneur démarré avant la fin des
    migrations répond à tout — et se trompe sur la moitié des requêtes.
    """
    checks: dict[str, object] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    state = revision_state(session)
    checks["schema"] = "ok" if state.up_to_date else "stale"
    checks["schema_detail"] = state.as_dict()

    ready = checks["database"] == "ok" and state.up_to_date
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not_ready", "checks": checks}


@router.get("/health", response_model=HealthResponse)
def health(session: SessionDep, settings: SettingsDep) -> HealthResponse:
    """Sonde publique. Ne révèle ni URL, ni jeton, ni nom d'hôte."""
    try:
        session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "error"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        environment=str(settings.environment),
        version=__version__,
        database=database,
        connectors={
            "crm": settings.crm_provider,
            "llm": settings.llm_provider,
            "search": settings.search_provider,
            "email": "disabled" if not settings.can_send_email() else settings.email_provider,
            "maintrix": "http" if settings.product_adapter_active("maintrix") else "mock",
            "educore": "http" if settings.product_adapter_active("educore") else "mock",
        },
        warnings=settings.configuration_warnings(),
    )


@router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def metrics(
    session: SessionDep,
    settings: SettingsDep,
    authorization: str = Header(default=""),
) -> PlainTextResponse:
    """Métriques au format Prometheus.

    Protégée par un jeton dédié plutôt que par le compte d'un utilisateur : un
    collecteur n'est pas une personne, et lui donner un rôle applicatif
    mélangerait supervision et autorisation métier.

    Sans `GA_METRICS_TOKEN`, l'endpoint est **refusé**, jamais ouvert. Ces
    mesures portent des chiffres commerciaux — nombre d'organisations
    contactées, d'oppositions, d'envois bloqués — et un endpoint de métriques
    laissé ouvert « le temps de configurer » ne se referme jamais.
    """
    if not settings.metrics_token:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Métriques non configurées.",
        )
    presented = authorization.removeprefix("Bearer ").strip()
    # Comparaison à temps constant : une comparaison naïve laisse mesurer le
    # préfixe correct, caractère par caractère.
    if not secrets.compare_digest(presented, settings.metrics_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton de métriques invalide.",
        )
    return PlainTextResponse(
        render_prometheus(collect(session, settings=settings)),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@admin_router.get("/alerts")
def alerts(
    session: SessionDep,
    settings: SettingsDep,
    _: CurrentUser = Depends(require(CAN_READ)),
) -> dict[str, object]:
    """Alertes actuellement déclenchées, avec le geste à faire.

    Existe pour que la supervision ne suppose pas une pile Prometheus : tout
    le monde n'en a pas une, et une alerte qui exige un outillage complet
    n'alerte personne chez qui il manque.

    Chaque alerte porte son `runbook`. Une alerte sans geste associé réveille
    quelqu'un qui ne saura pas quoi faire, et finit par être ignorée.
    """
    samples = collect(session, settings=settings)
    firing = evaluate_alerts(samples)
    return {
        "firing": firing,
        "count": len(firing),
        "worst_severity": firing[0]["severity"] if firing else "ok",
        "measurements": [
            {
                "name": sample.name,
                "labels": sample.labels,
                # Même raison qu'au-dessus : un infini sérialisé en JSON
                # ressort en `null`, qui se lit « aucune mesure ».
                "value": None if is_infinite(sample.value) else sample.value,
                "value_text": describe_value(sample.value),
            }
            for sample in samples
        ],
    }


@admin_router.get("/integrations")
def integrations(
    settings: SettingsDep,
    _: CurrentUser = Depends(require(CAN_READ)),
):
    """État déclaré des intégrations, sans aucun secret.

    On expose le fait qu'une clé est renseignée, jamais sa valeur ni sa
    longueur.
    """
    return {
        "crm": {
            "provider": settings.crm_provider,
            "credential_configured": bool(settings.crm_api_token),
            "mode": "réel"
            if settings.crm_provider != "mock" and settings.crm_api_token
            else "simulé",
        },
        "llm": {
            "provider": settings.llm_provider,
            "credential_configured": bool(settings.llm_api_key),
            "daily_token_budget": settings.llm_daily_token_budget,
        },
        "search": {
            "provider": settings.search_provider,
            "credential_configured": bool(settings.search_api_key),
        },
        "email": {
            "provider": settings.email_provider,
            "sending_enabled": settings.email_sending_enabled,
            "effective_mode": "réel" if settings.can_send_email() else "simulé",
            "max_per_contact_per_30_days": settings.email_max_per_contact_per_30_days,
            "max_per_campaign_per_day": settings.email_max_per_campaign_per_day,
        },
        "maintrix": {
            "adapter": settings.maintrix_adapter,
            "enabled": settings.maintrix_enabled,
            "active": settings.product_adapter_active("maintrix"),
            "access": "lecture seule",
            "authorization_required": True,
            "note": "Activation soumise à autorisation — voir docs/11-dependances-autorisation.md.",
        },
        "educore": {
            "adapter": settings.educore_adapter,
            "enabled": settings.educore_enabled,
            "active": settings.product_adapter_active("educore"),
            "access": "lecture seule",
            "authorization_required": True,
            "note": "Activation soumise à autorisation — voir docs/11-dependances-autorisation.md.",
        },
    }


@admin_router.get("/retention")
def retention_plan(
    session: SessionDep,
    settings: SettingsDep,
    user: CurrentUser = Depends(require(CAN_ADMINISTER)),
):
    """Ce que la purge ferait si elle s'exécutait maintenant. N'écrit rien.

    Il n'existe volontairement **aucun endpoint pour déclencher une purge
    réelle**. Une suppression définitive ne doit pas tenir à un bouton dans un
    navigateur : elle passe par la ligne de commande et la tâche planifiée,
    où elle est délibérée, tracée et reproductible.
    """
    from growth_agent.retention.runner import active_run
    from growth_agent.retention.service import PurgeService

    plan = PurgeService(session, settings=settings).plan()
    running = active_run(session, settings)

    return {
        "enabled": settings.purge_enabled,
        "dry_run_by_default": settings.purge_dry_run_default,
        "safeguards": {
            "max_records_per_run": settings.purge_max_records_per_run,
            "max_table_ratio": settings.purge_max_table_ratio,
            "batch_size": settings.purge_batch_size,
        },
        "run_in_progress": str(running.id) if running else None,
        "plan": plan.as_dict(),
        "note": (
            "Calcul en lecture seule. L'exécution réelle passe par "
            "`python -m growth_agent.retention.cli --apply`, déclenchée par la "
            "tâche planifiée."
        ),
    }


@admin_router.get("/retention/runs")
def retention_runs(
    session: SessionDep,
    limit: int = 20,
    user: CurrentUser = Depends(require(CAN_ADMINISTER)),
):
    """Historique des exécutions de purge."""
    from sqlalchemy import select

    from growth_agent.models.ops import PurgeRun

    runs = session.execute(
        select(PurgeRun).order_by(PurgeRun.started_at.desc()).limit(min(limit, 200))
    ).scalars()

    return [
        {
            "id": str(run.id),
            "status": run.status,
            "dry_run": run.dry_run,
            "triggered_by": run.triggered_by,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "counts": run.counts,
            "total_records": run.total_records,
            "stopped_reason": run.stopped_reason,
            "resumed_from_id": str(run.resumed_from_id) if run.resumed_from_id else None,
        }
        for run in runs
    ]
