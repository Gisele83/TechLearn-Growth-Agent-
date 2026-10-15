"""Gestion uniforme des erreurs.

Une erreur rendue à l'utilisateur ne contient jamais de trace, de requête SQL
ni de valeur de configuration : seulement un code exploitable par l'interface,
un message en français et, si utile, une indication de correction.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from growth_agent.agents.outreach_composer import CompositionRefused
from growth_agent.agents.proposal_assistant import PricingError
from growth_agent.connectors.base import ConnectorDisabledError, ConnectorError, RateLimitError
from growth_agent.connectors.email.base import EmailSendingDisabled
from growth_agent.security.rbac import PermissionDenied
from growth_agent.services.approvals import ApprovalError, SelfApprovalError
from growth_agent.services.outreach import SendBlocked

logger = logging.getLogger("growth_agent.errors")


def _payload(code: str, detail: str, hint: str = "") -> dict[str, str]:
    return {"code": code, "detail": detail, "hint": hint}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PermissionDenied)
    async def _permission_denied(_: Request, exc: PermissionDenied) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=_payload(
                "permission_denied",
                str(exc),
                f"Demander le rôle « {exc.required} » à un administrateur.",
            ),
        )

    @app.exception_handler(SelfApprovalError)
    async def _self_approval(_: Request, exc: SelfApprovalError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload(
                "self_approval_forbidden",
                str(exc),
                "Faire valider par une autre personne disposant du rôle commercial.",
            ),
        )

    @app.exception_handler(ApprovalError)
    async def _approval_error(_: Request, exc: ApprovalError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload("approval_error", str(exc)),
        )

    @app.exception_handler(SendBlocked)
    async def _send_blocked(_: Request, exc: SendBlocked) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload(exc.reason_code, str(exc)),
        )

    @app.exception_handler(CompositionRefused)
    async def _composition_refused(_: Request, exc: CompositionRefused) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload("composition_refused", str(exc)),
        )

    @app.exception_handler(PricingError)
    async def _pricing_error(_: Request, exc: PricingError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload("pricing_error", str(exc)),
        )

    @app.exception_handler(EmailSendingDisabled)
    async def _email_disabled(_: Request, exc: EmailSendingDisabled) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_payload(
                "email_sending_disabled",
                str(exc),
                "L'envoi réel reste désactivé tant que le domaine et le cadre juridique "
                "ne sont pas validés.",
            ),
        )

    @app.exception_handler(ConnectorDisabledError)
    async def _connector_disabled(_: Request, exc: ConnectorDisabledError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_payload("connector_disabled", str(exc)),
        )

    @app.exception_handler(RateLimitError)
    async def _rate_limited(_: Request, exc: RateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=_payload("rate_limited", str(exc), "Réessayer dans quelques instants."),
        )

    @app.exception_handler(ConnectorError)
    async def _connector_error(_: Request, exc: ConnectorError) -> JSONResponse:
        logger.warning("Erreur connecteur %s", exc.provider or "inconnu")
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_payload("connector_error", str(exc)),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error(_: Request, __: IntegrityError) -> JSONResponse:
        # Le message d'origine peut contenir des valeurs de colonnes : on ne le
        # renvoie pas.
        logger.warning("Violation de contrainte d'intégrité")
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_payload(
                "conflict",
                "Cet enregistrement existe déjà ou viole une contrainte d'unicité.",
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        logger.exception("Erreur base de données")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("database_error", "Erreur interne de persistance."),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) for e in exc.errors()})
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_payload(
                "validation_error",
                "Requête invalide.",
                "Champs en cause : " + ", ".join(f for f in fields if f),
            ),
        )

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=_payload("invalid_input", str(exc)),
        )
