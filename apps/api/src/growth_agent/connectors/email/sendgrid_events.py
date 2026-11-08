"""Événements d'expédition renvoyés par SendGrid : signature, puis lecture.

Jusqu'ici, tout ce qui suivait le premier contact était **déclaré** : une
personne saisissait la réponse, le rendez-vous, la démonstration. Le système
ne constatait que deux choses — qu'il avait remis un message au fournisseur,
et qu'il en avait bloqué un. Ce module ajoute ce que le fournisseur, lui,
constate : remis, rejeté, ouvert, signalé comme indésirable.

**Rien n'est accepté sans signature.** SendGrid signe chaque livraison avec
une clé ECDSA dont la partie publique est affichée dans sa console. Sans
cette clé en configuration, la route refuse tout : un point d'entrée public
qui écrit en base sur simple présentation d'un JSON est une porte ouverte à
qui veut fausser les indicateurs ou inscrire des oppositions au hasard.

**La fenêtre temporelle est aussi un contrôle.** Une requête authentique
interceptée reste authentique : sans borne d'âge, elle se rejoue
indéfiniment. L'identifiant d'événement protège l'idempotence côté stockage,
la fenêtre protège ce qui arrive avant.
"""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_der_public_key

#: En-têtes posés par SendGrid sur chaque livraison signée.
SIGNATURE_HEADER = "X-Twilio-Email-Event-Webhook-Signature"
TIMESTAMP_HEADER = "X-Twilio-Email-Event-Webhook-Timestamp"

#: Dix minutes. Plus large que les 5 minutes des webhooks produits : SendGrid
#: réessaie une livraison en échec, et une fenêtre trop étroite transformerait
#: chaque incident réseau en événements définitivement perdus.
DEFAULT_TOLERANCE_SECONDS = 600


class SendGridWebhookError(Exception):
    """Signature absente, invalide, ou horodatage hors fenêtre."""


@dataclass(frozen=True, slots=True)
class ProviderEvent:
    """Un événement d'expédition, réduit à ce dont nous avons besoin.

    Ce que SendGrid envoie d'autre — l'adresse, le motif en clair, l'agent
    utilisateur, l'adresse IP — n'entre pas ici. Une structure qui ne porte
    pas une donnée ne peut pas la laisser fuir plus loin.
    """

    event_id: str
    #: Nom SendGrid, brut : « delivered », « bounce », « spamreport »…
    kind: str
    #: Identifiant du message, **sans** le suffixe que SendGrid ajoute.
    message_id: str
    occurred_at: datetime
    #: « bounce » (rejet définitif), « blocked » (refus temporaire), ou vide.
    bounce_type: str = ""


def verify_event_signature(
    *,
    public_key_b64: str,
    signature_b64: str,
    timestamp: str,
    body: bytes,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Lève `SendGridWebhookError` si la livraison n'est pas authentique.

    `body` doit être le corps **brut** de la requête. Le resérialiser depuis
    un dictionnaire change un espace ou un ordre de clé, et la signature ne
    correspond plus — avec, à la clé, un rejet qu'on met des heures à
    comprendre.
    """
    if not public_key_b64:
        raise SendGridWebhookError(
            "Aucune clé publique configurée : les événements d'expédition sont refusés."
        )
    if not signature_b64 or not timestamp:
        raise SendGridWebhookError("Signature ou horodatage manquant.")

    try:
        sent_at = float(timestamp)
    except ValueError as exc:
        raise SendGridWebhookError("Horodatage illisible.") from exc

    current = now if now is not None else time.time()
    if abs(current - sent_at) > tolerance_seconds:
        raise SendGridWebhookError(
            f"Horodatage hors fenêtre de {tolerance_seconds} s : "
            "requête rejouée ou horloge décalée."
        )

    try:
        key = load_der_public_key(base64.b64decode(public_key_b64, validate=True))
    except (ValueError, binascii.Error, TypeError) as exc:
        raise SendGridWebhookError("Clé publique illisible.") from exc
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise SendGridWebhookError("La clé configurée n'est pas une clé ECDSA.")

    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SendGridWebhookError("Signature illisible.") from exc

    try:
        key.verify(signature, timestamp.encode("ascii") + body, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise SendGridWebhookError("Signature invalide.") from exc


def _message_base(sg_message_id: str) -> str:
    """« abc123.filterdrecv-5f8… » → « abc123 ».

    SendGrid renvoie dans ses événements un identifiant suffixé par le nœud
    qui a traité le message, alors que l'API d'envoi a répondu l'identifiant
    nu. Comparer les deux tels quels ne rapproche jamais rien, et le symptôme
    est silencieux : des événements qui arrivent, et aucun qui se rattache.
    """
    return sg_message_id.split(".", 1)[0].strip()


def parse_events(payload: object) -> list[ProviderEvent]:
    """Lit une livraison SendGrid et écarte à la frontière ce qui ne s'y lit pas.

    Un événement mal formé est ignoré, pas fatal : refuser toute la livraison
    pour une ligne douteuse ferait perdre les autres, et SendGrid rejouerait
    le lot entier sans fin.
    """
    if not isinstance(payload, list):
        return []

    events: list[ProviderEvent] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        event_id = str(entry.get("sg_event_id") or "").strip()
        kind = str(entry.get("event") or "").strip().lower()
        message_id = _message_base(str(entry.get("sg_message_id") or ""))
        if not event_id or not kind or not message_id:
            continue
        try:
            moment = datetime.fromtimestamp(float(entry.get("timestamp", 0)), tz=UTC)
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        events.append(
            ProviderEvent(
                event_id=event_id[:128],
                kind=kind,
                message_id=message_id[:255],
                occurred_at=moment,
                bounce_type=str(entry.get("type") or "").strip().lower()[:32],
            )
        )
    return events
