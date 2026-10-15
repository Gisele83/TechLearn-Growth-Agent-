"""Éléments communs à tous les connecteurs."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")


class ConnectorError(Exception):
    """Erreur d'un connecteur externe."""

    def __init__(self, message: str, *, provider: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class RateLimitError(ConnectorError):
    """Quota atteint côté fournisseur (HTTP 429)."""

    def __init__(self, message: str, *, provider: str = "", retry_after: float = 1.0) -> None:
        super().__init__(message, provider=provider, retryable=True)
        self.retry_after = retry_after


class ConnectorDisabledError(ConnectorError):
    """Le connecteur est volontairement désactivé par la configuration.

    Levée plutôt que de tenter un appel : c'est le garde-fou qui empêche un
    appel accidentel vers Maintrix, EduCore ou un fournisseur payant.
    """


@dataclass(slots=True)
class Page:
    """Page de résultats d'une API paginée."""

    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None

    @property
    def has_more(self) -> bool:
        return self.next_cursor is not None


@dataclass(slots=True)
class CircuitBreaker:
    """Coupe-circuit simple.

    Après `failure_threshold` échecs consécutifs, les appels sont refusés
    pendant `reset_after_seconds`. Évite de marteler un fournisseur en panne et
    de bloquer les workflows derrière des délais d'attente.
    """

    failure_threshold: int = 5
    reset_after_seconds: float = 30.0
    _failures: int = 0
    _opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if (time.monotonic() - self._opened_at) >= self.reset_after_seconds:
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = time.monotonic()

    def ensure_closed(self, *, provider: str = "") -> None:
        """Refuse l'appel si le coupe-circuit est ouvert.

        À utiliser quand l'échec ne se manifeste pas par une exception mais par
        un code de statut HTTP : l'appelant décide alors lui-même, après
        lecture de la réponse, s'il s'agit d'un succès ou d'un échec.
        """
        if self.is_open:
            raise ConnectorError(
                f"Coupe-circuit ouvert pour {provider or 'le fournisseur'} : appel refusé.",
                provider=provider,
                retryable=True,
            )

    def call(self, func: Callable[[], T], *, provider: str = "") -> T:
        if self.is_open:
            raise ConnectorError(
                f"Coupe-circuit ouvert pour {provider or 'le fournisseur'} : appel refusé.",
                provider=provider,
                retryable=True,
            )
        try:
            result = func()
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


def backoff_delays(
    max_retries: int, *, base: float = 0.5, cap: float = 8.0
) -> list[float]:
    """Délais d'attente exponentiels bornés, déterministes donc testables."""
    return [min(cap, base * (2**attempt)) for attempt in range(max_retries)]
