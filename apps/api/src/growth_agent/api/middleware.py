"""Limitation de débit et identifiant de corrélation."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

#: Chemins exemptés : les sondes et le collecteur de métriques doivent rester
#: joignables sous charge. Les limiter revient à se rendre aveugle au moment
#: précis où l'on a besoin de voir — et à faire retirer du trafic une instance
#: qui n'avait pour tout défaut que d'être occupée.
EXEMPT_PATHS = frozenset(
    {
        "/health",
        "/health/live",
        "/health/ready",
        "/metrics",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attache un identifiant à chaque requête, repris dans la réponse.

    Permet de relier une erreur signalée par un utilisateur aux entrées de
    journal correspondantes, sans avoir à y écrire la moindre donnée métier.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fenêtre glissante en mémoire, par adresse source.

    Suffisant pour une instance unique. Derrière plusieurs répliques, ce
    compteur devient approximatif : il faudra alors le déporter dans Redis
    (voir docs/09, section « montée en charge »).
    """

    def __init__(self, app, *, max_requests: int = 120, window_seconds: float = 60.0) -> None:
        super().__init__(app)
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        client = request.client.host if request.client else "inconnu"
        now = time.monotonic()
        hits = self._hits[client]
        while hits and now - hits[0] > self._window:
            hits.popleft()

        if len(hits) >= self._max:
            retry_after = max(1, int(self._window - (now - hits[0])))
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "code": "rate_limited",
                    "detail": "Trop de requêtes.",
                    "hint": f"Réessayer dans {retry_after} seconde(s).",
                },
                headers={"Retry-After": str(retry_after)},
            )

        hits.append(now)
        return await call_next(request)
