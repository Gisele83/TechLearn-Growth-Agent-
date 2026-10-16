"""Envoi via l'API SendGrid v3 — inactif tant qu'il n'est pas autorisé.

Comme le fournisseur SMTP, le constructeur refuse de s'initialiser si
`email_sending_enabled` est faux : le chemin d'envoi réel ne doit pas pouvoir
être emprunté par erreur.

Pourquoi l'API plutôt que le relais SMTP de SendGrid, qui aurait demandé zéro
ligne de code : parce que le protocole d'envoi du service distingue trois
issues, et que seule l'API permet de les distinguer franchement.

* **Refus explicite** (4xx) — adresse invalide, expéditeur non vérifié, charge
  rejetée. Rien n'est parti ; la réservation est libérée et une nouvelle
  tentative reste possible une fois la cause levée.
* **Panne** (5xx, coupure, délai dépassé) — issue inconnue. Le message est
  peut-être parti, il ne sera jamais rejoué automatiquement.
* **Accepté** (202) — SendGrid a pris le message en charge.

Au-dessus d'un relais SMTP, un rejet définitif remonte comme une exception et
se confond avec une coupure réseau : le message finirait en « issue
inconnue », bloqué pour toujours en attente d'un arbitrage humain qui n'avait
pas lieu d'être.

Le mode bac à sable (`sandbox_mode`) fait valider la requête par SendGrid
**sans rien livrer**. C'est ce qui permet d'éprouver l'intégration réelle —
authentification, expéditeur vérifié, forme de la charge — sans qu'un seul
message n'atteigne qui que ce soit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from growth_agent.connectors.base import TokenBucket, backoff_delays
from growth_agent.connectors.email.base import (
    EmailMessage,
    EmailProvider,
    EmailSendingDisabled,
    EmailSendResult,
)

API_PATH = "/v3/mail/send"

#: En-têtes que SendGrid refuse qu'on lui impose : il les pose lui-même.
RESERVED_HEADERS = frozenset({"to", "from", "subject", "cc", "bcc", "reply-to"})


class SendGridEmailProvider(EmailProvider):
    provider = "sendgrid"

    def __init__(
        self,
        *,
        api_key: str,
        from_email: str,
        sending_enabled: bool,
        from_name: str = "",
        reply_to: str = "",
        base_url: str = "https://api.sendgrid.com",
        sandbox_mode: bool = False,
        max_retries: int = 3,
        timeout: float = 20.0,
        rate_limit_per_second: float = 5.0,
        client: httpx.Client | None = None,
        sleep: Any = None,
    ) -> None:
        if not sending_enabled:
            raise EmailSendingDisabled(
                "GA_EMAIL_SENDING_ENABLED est faux : l'envoi réel est interdit."
            )
        if not (api_key and from_email):
            raise EmailSendingDisabled("Configuration SendGrid incomplète : envoi refusé.")

        import time

        self._from_email = from_email
        self._from_name = from_name
        self._reply_to = reply_to
        self._sandbox = sandbox_mode
        self._max_retries = max_retries
        self._sleep = sleep or time.sleep
        self._limiter = TokenBucket(rate_limit_per_second, sleep=self._sleep)
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    # --- Envoi ---------------------------------------------------------------
    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        payload = self._payload(message, idempotency_key)
        delays = backoff_delays(self._max_retries)

        for attempt in range(self._max_retries + 1):
            self._limiter.acquire()
            response = self._client.post(API_PATH, json=payload)

            if response.status_code in (200, 202):
                return EmailSendResult(
                    accepted=True,
                    provider=self.provider,
                    message_id=response.headers.get("X-Message-Id", "")[:255],
                    sent_at=datetime.now(UTC),
                    detail=(
                        "Bac à sable SendGrid : la requête a été validée, rien n'a été livré."
                        if self._sandbox
                        else "Message remis à SendGrid."
                    ),
                )

            if response.status_code == 429 or response.status_code >= 500:
                # Transitoire. Si les réessais s'épuisent, on laisse remonter :
                # le service d'envoi classera l'issue comme inconnue, ce qui
                # est exact — on ne sait pas si SendGrid a pris le message.
                if attempt < self._max_retries:
                    self._sleep(_retry_after(response, default=delays[attempt]))
                    continue
                raise SendGridUnavailable(
                    f"SendGrid indisponible après {self._max_retries} réessais "
                    f"(dernier code {response.status_code})."
                )

            # 4xx hors 429 : la requête est fautive, pas le service. Rien n'est
            # parti, et réessayer ne ferait que consommer du quota.
            return EmailSendResult(
                accepted=False,
                provider=self.provider,
                detail=_refusal_detail(response),
            )

        raise SendGridUnavailable("SendGrid : issue indéterminée.")

    def _payload(self, message: EmailMessage, idempotency_key: str) -> dict[str, Any]:
        sender: dict[str, str] = {"email": message.from_email or self._from_email}
        if self._from_name:
            sender["name"] = self._from_name

        payload: dict[str, Any] = {
            "personalizations": [{"to": [{"email": message.to_email}]}],
            "from": sender,
            "subject": message.subject,
            "content": [{"type": "text/plain", "value": message.body}],
            # Repère dans le journal d'activité SendGrid. C'est un condensat,
            # pas une donnée personnelle : il permet de relier un message
            # distant à notre réservation sans y écrire d'adresse.
            "custom_args": {"idempotency_key": idempotency_key[:100]},
            # Jamais de suivi : un pixel de traçage dans un message de
            # prospection collecte des données sans consentement, et les
            # liens réécrits font passer la désinscription par un tiers.
            "tracking_settings": {
                "click_tracking": {"enable": False, "enable_text": False},
                "open_tracking": {"enable": False},
                "subscription_tracking": {"enable": False},
            },
        }

        reply_to = message.reply_to or self._reply_to
        if reply_to:
            payload["reply_to"] = {"email": reply_to}

        headers = {
            key: value
            for key, value in message.headers.items()
            if key.lower() not in RESERVED_HEADERS
        }
        if headers:
            payload["headers"] = headers

        if self._sandbox:
            payload["mail_settings"] = {"sandbox_mode": {"enable": True}}
        return payload

    def healthcheck(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": "configured",
            "sandbox_mode": self._sandbox,
            # Ni la clé, ni sa longueur : les deux renseignent un curieux.
            "sender_domain": self._from_email.rpartition("@")[2],
        }

    def close(self) -> None:
        self._client.close()


class SendGridUnavailable(Exception):
    """SendGrid n'a pas répondu de façon lisible.

    Volontairement une exception plutôt qu'un `EmailSendResult` refusé : le
    service d'envoi doit classer cette issue comme **inconnue**, pas comme un
    échec. Le message est peut-être parti.
    """


def _retry_after(response: httpx.Response, *, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def _refusal_detail(response: httpx.Response) -> str:
    """Motif lisible, sans recopier une réponse entière dans nos journaux."""
    try:
        errors = response.json().get("errors", [])
    except ValueError:
        errors = []
    messages = [str(error.get("message", "")).strip() for error in errors if error.get("message")]
    if messages:
        return f"SendGrid a refusé le message ({response.status_code}) : {'; '.join(messages[:3])}"
    return f"SendGrid a refusé le message ({response.status_code})."
