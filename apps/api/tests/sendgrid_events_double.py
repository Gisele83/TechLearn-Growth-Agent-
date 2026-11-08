"""Doublure du « Signed Event Webhook » de SendGrid.

Elle ne simule pas la vérification : elle **signe réellement**, avec une
paire de clés ECDSA engendrée pour le test. C'est la différence entre
éprouver le code de vérification et éprouver une doublure qui dit oui.

Aucun envoi n'a lieu, et aucune clé réelle n'entre ici : la paire naît dans
le test et meurt avec lui.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from growth_agent.connectors.email.sendgrid_events import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
)


@dataclass(frozen=True, slots=True)
class SignedDelivery:
    """Ce que SendGrid pose sur le réseau : un corps, et deux en-têtes."""

    body: bytes
    headers: dict[str, str]


class SendGridSigner:
    """Engendre une paire ECDSA et signe comme SendGrid le fait."""

    def __init__(self) -> None:
        self._private = ec.generate_private_key(ec.SECP256R1())

    @property
    def public_key_b64(self) -> str:
        """La clé telle que la console SendGrid l'affiche : base64 d'un DER."""
        der = self._private.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return base64.b64encode(der).decode("ascii")

    def sign_body(self, body: bytes, *, timestamp: float | None = None) -> SignedDelivery:
        """Signe un corps quelconque — y compris illisible, pour éprouver
        l'ordre des contrôles : la signature d'abord, la lecture ensuite."""
        moment = str(int(timestamp if timestamp is not None else time.time()))
        signature = self._private.sign(moment.encode("ascii") + body, ec.ECDSA(hashes.SHA256()))
        return SignedDelivery(
            body=body,
            headers={
                SIGNATURE_HEADER: base64.b64encode(signature).decode("ascii"),
                TIMESTAMP_HEADER: moment,
                "Content-Type": "application/json",
            },
        )

    def sign(self, events: list[dict], *, timestamp: float | None = None) -> SignedDelivery:
        # `separators` reproduit un corps compact : ce qui compte est que le
        # corps signé soit **exactement** celui qui part sur le réseau.
        body = json.dumps(events, separators=(",", ":")).encode("utf-8")
        return self.sign_body(body, timestamp=timestamp)


def event(
    *,
    message_id: str,
    kind: str = "delivered",
    event_id: str = "evt-1",
    at: float | None = None,
    bounce_type: str = "",
    email: str = "destinataire@exemple.example.org",
    reason: str = "",
    suffixed: bool = True,
) -> dict:
    """Un événement au format SendGrid.

    `suffixed` reproduit le détail qui fait tout échouer en silence :
    SendGrid renvoie `sg_message_id` suffixé par le nœud qui a traité le
    message, alors que l'API d'envoi a répondu l'identifiant nu.
    """
    charge = {
        "email": email,
        "event": kind,
        "sg_event_id": event_id,
        "sg_message_id": (f"{message_id}.filterdrecv-abc-1234-5678" if suffixed else message_id),
        "timestamp": int(at if at is not None else time.time()),
    }
    if bounce_type:
        charge["type"] = bounce_type
    if reason:
        charge["reason"] = reason
    return charge
