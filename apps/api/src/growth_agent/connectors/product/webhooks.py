"""Vérification de signature des webhooks produits.

Un webhook non signé, mal signé ou rejoué est rejeté. La fenêtre temporelle
empêche la réutilisation d'une requête interceptée ; l'identifiant d'événement
assure l'idempotence côté stockage (contrainte d'unicité sur
`product_pilot_events.external_event_id`).
"""

from __future__ import annotations

import hashlib
import hmac
import time


class WebhookVerificationError(Exception):
    """Signature absente, invalide ou horodatage hors fenêtre."""


DEFAULT_TOLERANCE_SECONDS = 300


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """`HMAC-SHA256(secret, "<timestamp>.<corps>")`, en hexadécimal."""
    message = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    *,
    secret: str,
    timestamp: str,
    signature: str,
    body: bytes,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Lève `WebhookVerificationError` si la requête n'est pas authentique."""
    if not secret:
        raise WebhookVerificationError(
            "Aucun secret configuré pour ce produit : webhook refusé."
        )
    if not signature or not timestamp:
        raise WebhookVerificationError("Signature ou horodatage manquant.")

    try:
        sent_at = float(timestamp)
    except ValueError as exc:
        raise WebhookVerificationError("Horodatage illisible.") from exc

    current = now if now is not None else time.time()
    if abs(current - sent_at) > tolerance_seconds:
        raise WebhookVerificationError(
            f"Horodatage hors fenêtre de {tolerance_seconds} s : requête rejouée ou horloge décalée."
        )

    expected = compute_signature(secret, timestamp, body)
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("Signature invalide.")
