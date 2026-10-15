"""Prospects, sources, qualification et correspondance CRM.

Le CRM reste la source de vérité pour les entreprises, contacts et opportunités.
Les tables ci-dessous conservent uniquement ce qui est nécessaire au travail de
l'agent — provenance, qualification, décisions — plus une table de
correspondance d'identifiants (`crm_mappings`) pour éviter de recopier le CRM.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from growth_agent.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from growth_agent.db.types import GUID, JSONDict
from growth_agent.models.enums import (
    LegalBasis,
    ProspectStage,
    VerificationStatus,
)


class ProspectCandidate(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Organisation repérée pour un produit donné.

    `dedupe_key` est une empreinte normalisée (domaine, ou nom + pays à défaut)
    qui rend le dédoublonnage déterministe et indexable.
    """

    __tablename__ = "prospect_candidates"
    __table_args__ = (
        UniqueConstraint("product_id", "dedupe_key", name="uq_prospect_product_dedupe"),
        Index("ix_prospect_stage", "product_id", "stage"),
        Index("ix_prospect_score", "product_id", "score"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )

    organization_name: Mapped[str] = mapped_column(String(256), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(256), nullable=False)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(256), nullable=True)
    country_code: Mapped[str] = mapped_column(String(2), default="FR", nullable=False)
    city: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(256), nullable=True)
    headcount_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Contact principal. Volontairement minimal : nom, fonction, email
    # professionnel. Aucune donnée personnelle sensible n'est stockée ici.
    contact_full_name: Mapped[str | None] = mapped_column(String(192), nullable=True)
    contact_role: Mapped[str | None] = mapped_column(String(192), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)

    stage: Mapped[str] = mapped_column(
        String(24), default=ProspectStage.DISCOVERED, nullable=False
    )
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNKNOWN, nullable=False
    )
    legal_basis: Mapped[str] = mapped_column(
        String(32), default=LegalBasis.UNDETERMINED, nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discovered_by: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)

    sources: Mapped[list[ProspectSource]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan"
    )
    assessments: Mapped[list[QualificationAssessment]] = relationship(
        back_populates="prospect", cascade="all, delete-orphan"
    )


class ProspectSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Preuve de provenance. Sans au moins une source, un prospect reste inconnu."""

    __tablename__ = "prospect_sources"
    __table_args__ = (
        UniqueConstraint("prospect_id", "url", name="uq_source_prospect_url"),
        Index("ix_source_prospect", "prospect_id"),
    )

    prospect_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_by: Mapped[str] = mapped_column(String(64), default="market_scout", nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="mock", nullable=False)
    license_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    prospect: Mapped[ProspectCandidate] = relationship(back_populates="sources")


class QualificationAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Évaluation datée et explicable d'un prospect.

    `criteria` conserve le détail par critère (points obtenus, maximum, preuve
    citée, statut de vérification) pour que le score soit recalculable et
    contestable.
    """

    __tablename__ = "qualification_assessments"
    __table_args__ = (Index("ix_assessment_prospect", "prospect_id", "created_at"),)

    prospect_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="CASCADE"), nullable=False
    )
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNKNOWN, nullable=False
    )
    confidence: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    criteria: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    unknowns: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    assessed_by: Mapped[str] = mapped_column(
        String(64), default="lead_intelligence", nullable=False
    )
    model_reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)

    prospect: Mapped[ProspectCandidate] = relationship(back_populates="assessments")


class CRMMapping(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Correspondance entre un objet local et son identifiant CRM.

    Permet de retrouver l'objet distant sans dupliquer ses champs : le CRM reste
    la source de vérité, Growth Agent ne garde que le lien et la date de
    dernière synchronisation.
    """

    __tablename__ = "crm_mappings"
    __table_args__ = (
        UniqueConstraint(
            "crm_provider", "object_type", "local_id", name="uq_crm_mapping_local"
        ),
        UniqueConstraint(
            "crm_provider", "object_type", "remote_id", name="uq_crm_mapping_remote"
        ),
    )

    crm_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    object_type: Mapped[str] = mapped_column(String(32), nullable=False)
    local_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False)
    remote_id: Mapped[str] = mapped_column(String(128), nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sync_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False)
