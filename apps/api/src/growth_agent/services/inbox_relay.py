"""Fraîcheur du relevé de la boîte de réponse.

Chaque message de prospection promet « répondez STOP ». La promesse repose
sur une personne qui lit une boîte, et le système ne peut pas lire à sa
place. Ce qu'il peut faire, c'est **constater qu'elle l'a déclaré**, et
refuser de relancer quelqu'un quand cette déclaration est trop vieille.

## Une déclaration n'est pas une preuve

Il faut le dire franchement, parce que le reste du code en dépend. Un appel
à `POST /api/v1/replies/relay-check` atteste qu'un utilisateur authentifié
**affirme** avoir relevé la boîte, à une date donnée. Il n'atteste pas que
la boîte a été ouverte, ni qu'elle a été lue, ni qu'elle était vide.

Aucune preuve technique de consultation n'est possible d'ici : elle
supposerait un accès au serveur de messagerie, que Growth Agent n'a pas et
ne demande pas. Tout ce qui suit mesure donc un **engagement déclaré**, et
les noms employés le disent — `declared_relay`, et non `relay`.

Ce que cela vaut quand même : une déclaration est nominative, horodatée et
journalisée. Son absence se voit. Quelqu'un qui déclarerait sans relever
engagerait sa responsabilité, ce qui est précisément le niveau de garantie
qu'une organisation peut offrir sur une tâche humaine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from growth_agent.config import Settings
from growth_agent.models.ops import AuditEvent

#: Action journalisée par une déclaration de relevé sans réponse à reporter.
RELAY_DECLARED_ACTION = "inbox.relay_declared"

#: Actions qui valent déclaration de relevé. Reporter une réponse en vaut
#: une : on ne reporte pas ce qu'on n'a pas lu.
RELAY_ACTIONS: tuple[str, ...] = (
    RELAY_DECLARED_ACTION,
    "inbox.relayed",  # nom historique, conservé pour les traces déjà écrites
    "conversation_analyst.classified",
)


@dataclass(frozen=True)
class RelayFreshness:
    """État du dernier relevé déclaré."""

    #: `None` quand aucune déclaration n'existe.
    declared_at: datetime | None
    age_seconds: float | None
    max_age_seconds: float
    fresh: bool
    #: Faux quand les messages n'invitent pas à répondre : il n'y a alors
    #: aucune boîte à relever, et rien à contrôler.
    applicable: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "declared_at": self.declared_at.isoformat() if self.declared_at else None,
            "age_seconds": self.age_seconds,
            "max_age_seconds": self.max_age_seconds,
            "fresh": self.fresh,
            "applicable": self.applicable,
            "nature": "déclaration nominative, pas une preuve de consultation",
        }


def last_declared_relay(session: Session) -> datetime | None:
    """Date de la dernière déclaration de relevé, tous auteurs confondus."""
    moment = session.execute(
        select(func.max(AuditEvent.occurred_at)).where(AuditEvent.action.in_(RELAY_ACTIONS))
    ).scalar_one_or_none()
    if moment is None:
        return None
    # SQLite rend des horodatages naïfs, PostgreSQL les rend avec fuseau.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def check_freshness(
    session: Session,
    *,
    settings: Settings,
    now: datetime | None = None,
) -> RelayFreshness:
    """Le relevé déclaré est-il assez récent pour qu'on ose relancer ?"""
    max_age = max(0.0, settings.inbox_relay_max_age_hours * 3600)

    if not settings.replies_are_monitored():
        # Les messages n'invitent pas à répondre : aucune boîte n'est
        # promise, aucun relevé n'est dû. Contrôler ici bloquerait des
        # envois pour une obligation qui n'existe pas.
        return RelayFreshness(
            declared_at=None,
            age_seconds=None,
            max_age_seconds=max_age,
            fresh=True,
            applicable=False,
        )

    moment = now or datetime.now(UTC)
    declared_at = last_declared_relay(session)
    if declared_at is None:
        return RelayFreshness(
            declared_at=None,
            age_seconds=None,
            max_age_seconds=max_age,
            fresh=False,
            applicable=True,
        )

    age = max(0.0, (moment - declared_at).total_seconds())
    return RelayFreshness(
        declared_at=declared_at,
        age_seconds=age,
        max_age_seconds=max_age,
        fresh=age <= max_age,
        applicable=True,
    )
