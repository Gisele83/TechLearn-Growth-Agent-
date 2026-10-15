"""Supervision : santé de l'application et état des connecteurs."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from growth_agent import __version__
from growth_agent.api.deps import SessionDep, SettingsDep, require
from growth_agent.schemas.auth import CurrentUser
from growth_agent.schemas.common import HealthResponse
from growth_agent.security.rbac import CAN_ADMINISTER, CAN_READ

#: Sonde publique, montée à la racine : un répartiteur de charge ou un
#: conteneur doit pouvoir l'interroger sans jeton ni préfixe de version.
router = APIRouter(tags=["système"])

#: Endpoints de supervision authentifiés, montés sous /api/v1.
admin_router = APIRouter(prefix="/system", tags=["système"])


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
