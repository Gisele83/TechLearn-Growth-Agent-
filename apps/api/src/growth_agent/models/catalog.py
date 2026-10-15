"""Produits, campagnes et profils de client idéal."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from growth_agent.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from growth_agent.db.types import GUID, JSONDict, UTCDateTime
from growth_agent.models.enums import CampaignStatus, ProductCode


class Product(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Maintrix ou EduCore. Cloisonne toutes les données commerciales."""

    __tablename__ = "products"

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sector: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    campaigns: Mapped[list[Campaign]] = relationship(back_populates="product")
    profiles: Mapped[list[IdealCustomerProfile]] = relationship(back_populates="product")

    def __repr__(self) -> str:  # pragma: no cover - confort de débogage
        return f"<Product {self.code}>"


class IdealCustomerProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Profil de client idéal : critères d'éligibilité et pondérations."""

    __tablename__ = "ideal_customer_profiles"
    __table_args__ = (UniqueConstraint("product_id", "name", name="uq_icp_product_name"),)

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    country_codes: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    target_sectors: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    target_roles: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    min_headcount: Mapped[int | None] = mapped_column(nullable=True)
    max_headcount: Mapped[int | None] = mapped_column(nullable=True)
    need_signals: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)
    exclusion_signals: Mapped[list] = mapped_column(JSONDict, default=list, nullable=False)

    product: Mapped[Product] = relationship(back_populates="profiles")


class Campaign(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Campagne commerciale. Toujours rattachée à un seul produit."""

    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("product_id", "slug", name="uq_campaign_product_slug"),
        Index("ix_campaigns_status", "status"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    icp_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("ideal_customer_profiles.id", ondelete="SET NULL"), nullable=True
    )
    slug: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(192), nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=CampaignStatus.DRAFT, nullable=False)
    legal_regime: Mapped[str] = mapped_column(String(8), default="FR", nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    target_metrics: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    owner_email: Mapped[str] = mapped_column(String(256), default="", nullable=False)

    product: Mapped[Product] = relationship(back_populates="campaigns")

    @property
    def product_code(self) -> ProductCode | None:
        return ProductCode(self.product.code) if self.product else None
