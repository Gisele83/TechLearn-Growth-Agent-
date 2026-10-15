"""Utilisateurs, exécutions d'agents, audit, configuration et événements produits."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from growth_agent.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from growth_agent.db.types import GUID, JSONDict, UTCDateTime
from growth_agent.models.enums import Role, WorkflowStatus


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Utilisateur applicatif.

    Seul un condensat du mot de passe est stocké. Les comptes de démonstration
    livrés avec le dépôt ne sont créés qu'en environnement local.
    """

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(192), default="", nullable=False)
    role: Mapped[str] = mapped_column(String(16), default=Role.LECTEUR, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class WorkflowRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Exécution d'un enchaînement d'agents, rejouable et traçable."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("ix_workflow_status", "status", "created_at"),
        UniqueConstraint("idempotency_key", name="uq_workflow_idempotency"),
    )

    workflow_name: Mapped[str] = mapped_column(String(96), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), default=WorkflowStatus.PENDING, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    input_summary: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    output_summary: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)


class AgentAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Action unitaire d'un agent.

    Conserve la consommation de jetons et le coût estimé pour que le budget LLM
    soit mesurable, et une référence au modèle utilisé pour la reproductibilité.
    """

    __tablename__ = "agent_actions"
    __table_args__ = (Index("ix_agent_action_name_time", "agent_name", "created_at"),)

    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=True
    )
    agent_name: Mapped[str] = mapped_column(String(48), nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    target_type: Mapped[str] = mapped_column(String(48), default="", nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="succeeded", nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    output_summary: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    model_reference: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)


class AuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Journal d'audit append-only.

    Ne contient ni secret ni contenu personnel : seulement qui a fait quoi, sur
    quel objet, quand, et un condensat permettant de rapprocher l'entrée de la
    donnée d'origine.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_time", "occurred_at"),
        Index("ix_audit_object", "object_type", "object_id"),
    )

    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    object_type: Mapped[str] = mapped_column(String(48), nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    product_code: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class IntegrationConfig(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """État déclaré d'une intégration. Ne stocke JAMAIS de secret.

    Les identifiants vivent dans l'environnement ou le gestionnaire de secrets.
    Cette table ne conserve que le nom de la variable à lire
    (`credential_env_var`) et l'état d'activation, de façon à rendre la
    configuration auditable sans l'exposer.
    """

    __tablename__ = "integration_configs"
    __table_args__ = (UniqueConstraint("key", name="uq_integration_key"),)

    key: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), default="mock", nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    credential_env_var: Mapped[str] = mapped_column(String(96), default="", nullable=False)
    options: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    authorization_reference: Mapped[str] = mapped_column(Text, default="", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    last_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)


class ProductPilotEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Événement de pilote ou de licence lu depuis Maintrix ou EduCore.

    Alimenté exclusivement en lecture, via un adaptateur produit ou un webhook
    signé. Growth Agent n'écrit jamais dans les plateformes d'origine.
    """

    __tablename__ = "product_pilot_events"
    __table_args__ = (
        UniqueConstraint("product_id", "external_event_id", name="uq_pilot_event_external"),
        Index("ix_pilot_event_time", "product_id", "occurred_at"),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    prospect_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("prospect_candidates.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_org_ref: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    source_adapter: Mapped[str] = mapped_column(String(32), default="mock", nullable=False)


class PurgeRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Exécution de la purge de rétention.

    Sert trois usages à la fois :

    * **verrou** — une exécution `running` dont le battement de cœur est récent
      empêche une seconde exécution concurrente ;
    * **reprise** — une exécution interrompue est détectée par son battement de
      cœur figé ; la purge étant idempotente, la relance repart naturellement
      des enregistrements encore échus ;
    * **traçabilité** — qui a lancé quoi, quand, combien d'enregistrements, et
      pour quel motif l'exécution s'est éventuellement arrêtée.
    """

    __tablename__ = "purge_runs"
    __table_args__ = (Index("ix_purge_run_status", "status", "started_at"),)

    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    triggered_by: Mapped[str] = mapped_column(String(128), default="scheduler", nullable=False)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    #: Mis à jour après chaque lot. Un battement figé révèle une interruption.
    heartbeat_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    #: Détail par classe de donnée : {"prospects_anonymized": 12, ...}
    counts: Mapped[dict] = mapped_column(JSONDict, default=dict, nullable=False)
    #: Garde-fou ayant interrompu l'exécution, le cas échéant.
    stopped_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    error_message: Mapped[str] = mapped_column(Text, default="", nullable=False)
    resumed_from_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)

    @property
    def total_records(self) -> int:
        return sum(int(v) for v in self.counts.values() if isinstance(v, (int, float)))
