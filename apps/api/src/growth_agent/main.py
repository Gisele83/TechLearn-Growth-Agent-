"""Point d'entrée de l'API Growth Agent."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from growth_agent import __version__
from growth_agent.api.errors import register_error_handlers
from growth_agent.api.middleware import RateLimitMiddleware, RequestIdMiddleware
from growth_agent.api.routers import (
    analytics,
    auth,
    catalog,
    outreach,
    pipeline,
    prospects,
    public,
    system,
)
from growth_agent.config import Environment, get_settings
from growth_agent.logging_config import configure_logging

logger = logging.getLogger("growth_agent")

DESCRIPTION = """
API commerciale de **TechLearn Growth Agent**.

Application autonome : elle n'accède à aucune base de Maintrix ou d'EduCore.
Les deux plateformes ne sont consultées qu'à travers un connecteur produit en
**lecture seule**, désactivé tant qu'une autorisation explicite n'a pas été
obtenue.

Trois règles structurent l'API :

* aucun message ne part sans approbation humaine enregistrée ;
* un contact opposé à la prospection n'est jamais sollicité ;
* les indicateurs sont recalculés depuis les événements, jamais stockés.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    configure_logging(settings.log_level)
    logger.info(
        "Démarrage de Growth Agent %s — environnement %s", __version__, settings.environment
    )
    for warning in settings.configuration_warnings():
        logger.warning("Configuration : %s", warning)
    if settings.can_send_email():
        logger.warning(
            "L'ENVOI RÉEL D'EMAILS EST ACTIF (fournisseur %s). "
            "Vérifier le cadre juridique et la liste d'opposition.",
            settings.email_provider,
        )
    else:
        logger.info("Envoi d'emails en mode simulé : aucun message ne quittera la machine.")
    for product in ("maintrix", "educore"):
        if settings.product_adapter_active(product):
            logger.warning(
                "Connecteur %s ACTIF en lecture : vérifier que l'autorisation est bien "
                "formalisée (docs/11-dependances-autorisation.md).",
                product,
            )
    yield
    logger.info("Arrêt de Growth Agent.")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="TechLearn Growth Agent",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        # La documentation interactive n'est pas exposée en production.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=600,
    )

    register_error_handlers(app)

    app.include_router(system.router)
    # Publique, sans authentification : voir api/routers/public.py.
    app.include_router(public.router)
    app.include_router(system.admin_router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(catalog.router, prefix="/api/v1")
    app.include_router(prospects.router, prefix="/api/v1")
    app.include_router(outreach.router, prefix="/api/v1")
    app.include_router(analytics.router, prefix="/api/v1")
    app.include_router(pipeline.router, prefix="/api/v1")
    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "growth_agent.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.environment is Environment.LOCAL,
    )
