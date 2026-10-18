"""Opportunités et prochaines actions.

Jusqu'ici le parcours s'arrêtait au prospect. On savait qu'une organisation
avait été contactée, qu'elle avait répondu, qu'une démonstration était
prévue — mais pas **qui doit faire quoi, sur quel dossier, cette semaine**.
C'est pourtant la seule question que se pose une équipe commerciale le lundi
matin.

Deux objets y répondent.

`Opportunity` est le dossier : un prospect devenu une affaire, avec un
porteur, un montant, une date de clôture envisagée et, s'il se perd, un
motif. Il reste **local** : le CRM demeure la source de vérité des comptes
et des affaires qu'on y publie. Ce qui est ici sert à travailler et à
mesurer, pas à remplacer le CRM.

`NextAction` est l'engagement : une chose à faire, par quelqu'un, avant une
date. Les tâches créées jusqu'ici partaient dans le CRM et n'en revenaient
jamais — le tableau de bord ne pouvait pas les voir. Celles-ci restent
lisibles, et une action en retard se compte.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from growth_agent.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from growth_agent.db.types import GUID, JSONDict, UTCDateTime
from growth_agent.models.enums import (
    NextActionKind,
    NextActionStatus,
    OpportunityStage,
)


class Opportunity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Un dossier commercial, de la réponse à la décision."""

    __tablename__ = "opportunities"
    __table_args__ = (
        Index("ix_opportunity_stage", "product_id", "stage"),
        Index("ix_opportunity_owner", "owner_email", "stage"),
        # Un prospect n'a qu'un dossier ouvert à la fois. Deux dossiers sur
        # la même organisation, c'est deux personnes qui la relancent sans
        # le savoir.
        Index("ix_opportunity_prospect", "prospect_id"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    #: `SET NULL` et non `CASCADE` : la purge anonymise le prospect, et le
    #: dossier doit survivre pour que les indicateurs historiques tiennent.
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="SET NULL"), nullable=True
    )
    #: Nom de l'organisation au moment de l'ouverture. Dupliqué exprès : il
    #: doit rester lisible après l'anonymisation du prospect. Ce n'est pas
    #: une donnée personnelle — c'est une raison sociale.
    organization_name: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    stage: Mapped[str] = mapped_column(String(24), default=OpportunityStage.OPEN, nullable=False)
    owner_email: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="XAF", nullable=False)
    expected_close_on: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: Motif de perte, repris des objections classées. Sans lui, « perdu »
    #: n'apprend rien et ne permet aucune correction.
    loss_reason: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)

    next_actions: Mapped[list[NextAction]] = relationship(
        back_populates="opportunity", cascade="all, delete-orphan"
    )

    @property
    def is_closed(self) -> bool:
        return self.stage in {OpportunityStage.WON, OpportunityStage.LOST}


class NextAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Une chose à faire, par quelqu'un, avant une date."""

    __tablename__ = "next_actions"
    __table_args__ = (
        # L'index de la question du lundi matin : qu'est-ce qui est en retard,
        # et pour qui ?
        Index("ix_next_action_due", "status", "due_at"),
        Index("ix_next_action_owner", "owner_email", "status", "due_at"),
    )

    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("opportunities.id", ondelete="CASCADE"), nullable=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(24), default=NextActionKind.FOLLOW_UP, nullable=False)
    title: Mapped[str] = mapped_column(String(192), nullable=False)
    owner_email: Mapped[str] = mapped_column(String(256), default="", nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=NextActionStatus.OPEN, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    outcome: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Identifiant de la tâche jumelle dans le CRM, quand elle existe. Le
    #: dossier reste lisible ici même si le CRM est injoignable.
    crm_remote_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)

    opportunity: Mapped[Opportunity | None] = relationship(back_populates="next_actions")

    def is_overdue(self, now: datetime) -> bool:
        return self.status == NextActionStatus.OPEN and self.due_at < now
