"""Brouillons, approbations, événements d'envoi et liste d'opposition."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from growth_agent.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from growth_agent.db.types import GUID, JSONDict, UTCDateTime
from growth_agent.models.enums import (
    ApprovalDecision,
    DraftStatus,
    OutreachChannel,
    SuppressionScope,
)


class MessageTemplate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Gabarit de message, strictement rattaché à un produit.

    Les gabarits Maintrix et EduCore sont séparés : un gabarit ne peut pas être
    utilisé pour l'autre produit, ce qui évite les mélanges de discours et de
    base légale.
    """

    __tablename__ = "message_templates"
    __table_args__ = (UniqueConstraint("product_id", "slug", name="uq_template_product_slug"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(192), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default=OutreachChannel.EMAIL, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="fr", nullable=False)
    subject_template: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    allowed_variables: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class MessageDraft(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Brouillon de message. Ne peut devenir un envoi qu'après approbation."""

    __tablename__ = "message_drafts"
    __table_args__ = (
        Index("ix_draft_status", "status"),
        Index("ix_draft_prospect", "prospect_id", "created_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    prospect_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("message_templates.id", ondelete="SET NULL"), nullable=True
    )

    channel: Mapped[str] = mapped_column(String(16), default=OutreachChannel.EMAIL, nullable=False)
    recipient_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    subject: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), default="fr", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=DraftStatus.DRAFT, nullable=False)
    variables_used: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    generated_by: Mapped[str] = mapped_column(
        String(64), default="outreach_composer", nullable=False
    )
    model_reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    blocked_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    approvals: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )


class ApprovalRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Demande de validation humaine.

    Couvre tout ce qui engage l'entreprise : message sortant, devis, remise,
    contrat, activation de licence, modification d'infrastructure.
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_decision", "decision", "created_at"),
        Index("ix_approval_subject", "subject_type", "subject_id"),
    )

    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("message_drafts.id", ondelete="CASCADE"), nullable=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )

    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    decision: Mapped[str] = mapped_column(
        String(16), default=ApprovalDecision.PENDING, nullable=False
    )
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    draft: Mapped[MessageDraft | None] = relationship(back_populates="approvals")


class OutreachEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Événement du funnel. Les KPI sont recalculés à partir de cette table.

    Rien n'est agrégé en base : un compteur faux ne peut donc pas survivre à un
    recalcul, et tout indicateur affiché est traçable jusqu'à ses événements.
    """

    __tablename__ = "outreach_events"
    __table_args__ = (
        Index("ix_event_product_type_time", "product_id", "event_type", "occurred_at"),
        Index("ix_event_prospect", "prospect_id", "occurred_at"),
        UniqueConstraint("idempotency_key", name="uq_event_idempotency"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="CASCADE"), nullable=True
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("message_drafts.id", ondelete="SET NULL"), nullable=True
    )

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), default=OutreachChannel.EMAIL, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class SuppressionEntry(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Opposition à la prospection.

    Une entrée bloque les envois pour un email ou un domaine entier. La portée
    `global` s'applique aux deux produits ; `product` à un seul. Les entrées ne
    sont jamais supprimées automatiquement — seule une demande explicite les
    retire, et le retrait est journalisé.
    """

    __tablename__ = "suppression_entries"
    __table_args__ = (
        UniqueConstraint(
            "normalized_value", "scope", "product_id", name="uq_suppression_value_scope"
        ),
        Index("ix_suppression_lookup", "normalized_value", "scope"),
    )

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=True
    )
    scope: Mapped[str] = mapped_column(String(16), default=SuppressionScope.GLOBAL, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), default="email", nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(256), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(128), default="manual", nullable=False)
    note: Mapped[str] = mapped_column(Text, default="", nullable=False)
    effective_from: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
