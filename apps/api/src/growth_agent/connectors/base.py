"""Éléments communs à tous les connecteurs."""

from __future__ import annotations

import threading
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


class TokenBucket:
    """Limiteur de débit à jetons, sûr pour un usage concurrent.

    Un seau se remplit à `rate_per_second` jetons par seconde et en contient au
    plus `capacity`. Chaque appel en consomme un ; quand le seau est vide,
    l'appelant attend le temps nécessaire.

    La capacité permet une petite rafale après une période creuse — le
    comportement attendu d'un quota par seconde, qui n'interdit pas deux
    appels rapprochés, seulement un rythme soutenu trop élevé.

    `monotonic` et `sleep` sont injectables pour que les tests mesurent la
    décision du limiteur sans attendre réellement.
    """

    def __init__(
        self,
        rate_per_second: float,
        *,
        capacity: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("Le débit doit être strictement positif.")
        self.rate_per_second = rate_per_second
        self.capacity = capacity if capacity is not None else max(1.0, rate_per_second)
        self._monotonic = monotonic
        self._sleep = sleep
        self._tokens = self.capacity
        self._last = monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> float:
        """Consomme un jeton, en attendant s'il le faut. Renvoie l'attente."""
        with self._lock:
            # `_last` est une horloge *virtuelle* : elle peut être en avance
            # sur le temps réel, parce qu'un appelant qui attend a déjà réservé
            # sa part du débit. La comparer au temps réel sans ce `max` ferait
            # un écoulement négatif, un solde de jetons négatif, et des attentes
            # qui enflent à chaque appel au lieu de rester constantes.
            now = max(self._monotonic(), self._last)
            self._tokens = min(
                self.capacity, self._tokens + (now - self._last) * self.rate_per_second
            )
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            waited = (tokens - self._tokens) / self.rate_per_second
            # Le seau est débité et l'horloge avancée sous verrou : un second
            # appelant calcule son attente à partir de cet état, sinon les deux
            # attendraient la même durée et partiraient ensemble.
            self._tokens = 0.0
            self._last = now + waited
        self._sleep(waited)
        return waited


def backoff_delays(max_retries: int, *, base: float = 0.5, cap: float = 8.0) -> list[float]:
    """Délais d'attente exponentiels bornés, déterministes donc testables."""
    return [min(cap, base * (2**attempt)) for attempt in range(max_retries)]
