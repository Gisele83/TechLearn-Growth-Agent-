"""Points d'entrée appelés par des services extérieurs, sans authentification.

Un webhook ne peut pas présenter de jeton d'API : c'est un service distant
qui appelle, pas une personne connectée. L'authentification passe donc par
la signature de la requête, et la route est **fermée par défaut** — tant
qu'aucune clé de vérification n'est configurée, elle refuse tout plutôt que
d'accepter des événements que personne n'a signés.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from growth_agent.api.deps import SessionDep, SettingsDep
from growth_agent.connectors.email.sendgrid_events import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    SendGridWebhookError,
    parse_events,
    verify_event_signature,
)
from growth_agent.services.audit import record_audit
from growth_agent.services.email_events import ingest_provider_events

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/sendgrid/events")
async def sendgrid_events(
    request: Request, session: SessionDep, settings: SettingsDep
) -> JSONResponse:
    """Absorbe les événements d'expédition constatés par SendGrid.

    Renvoie 200 avec le détail de ce qui a été retenu, écarté ou rattaché à
    rien. SendGrid rejoue toute livraison non acquittée : répondre en erreur
    pour une ligne douteuse ferait revenir le lot entier indéfiniment, alors
    qu'un décompte explicite dit ce qui s'est passé sans rien perdre.
    """
    if not settings.sendgrid_event_public_key:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "disabled",
                "detail": "Absorption des événements d'expédition non configurée.",
            },
        )

    # Le corps **brut** : resérialiser le JSON changerait un espace ou un
    # ordre de clé, et la signature ne correspondrait plus.
    body = await request.body()
    try:
        verify_event_signature(
            public_key_b64=settings.sendgrid_event_public_key,
            signature_b64=request.headers.get(SIGNATURE_HEADER, ""),
            timestamp=request.headers.get(TIMESTAMP_HEADER, ""),
            body=body,
            tolerance_seconds=settings.sendgrid_event_tolerance_seconds,
        )
    except SendGridWebhookError as error:
        # Le motif est journalisé pour le diagnostic, mais pas renvoyé :
        # distinguer « signature invalide » de « horodatage périmé »
        # renseignerait qui cherche à forger une requête.
        await run_in_threadpool(_trace_refusal, session, str(error))
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"status": "rejected"})

    try:
        payload = json.loads(body)
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST, content={"status": "unreadable"}
        )

    events = parse_events(payload)
    report = await run_in_threadpool(_absorb, session, events, settings)
    return JSONResponse(status_code=status.HTTP_200_OK, content=report)


def _absorb(session, events, settings) -> dict:
    """Le travail en base, hors de la boucle d'événements.

    La session SQLAlchemy est synchrone : l'appeler depuis une coroutine
    bloquerait la boucle pour toute la durée d'un lot, qui peut porter
    plusieurs centaines d'événements.
    """
    report = ingest_provider_events(session, events=events, settings=settings)
    record_audit(
        session,
        actor="sendgrid",
        action="email_events.ingested",
        object_type="outreach_event",
        metadata=report.as_dict(),
    )
    session.flush()
    return report.as_dict()


def _trace_refusal(session, reason: str) -> None:
    record_audit(
        session,
        actor="inconnu",
        action="email_events.rejected",
        object_type="outreach_event",
        outcome="blocked",
        metadata={"reason": reason},
    )
    session.flush()
