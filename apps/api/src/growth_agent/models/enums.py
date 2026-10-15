"""Énumérations métier partagées."""

from __future__ import annotations

from enum import StrEnum


class ProductCode(StrEnum):
    MAINTRIX = "maintrix"
    EDUCORE = "educore"


class Role(StrEnum):
    """Rôles applicatifs, du plus large au plus restreint."""

    ADMIN = "admin"
    COMMERCIAL = "commercial"
    LECTEUR = "lecteur"


class CampaignStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class VerificationStatus(StrEnum):
    """Statut de vérification d'un prospect ou d'une donnée.

    `UNKNOWN` est la valeur par défaut : une information non étayée par une
    source reste inconnue, elle n'est jamais promue en hypothèse ou en preuve
    par un agent.
    """

    UNKNOWN = "unknown"
    HYPOTHESIS = "hypothesis"
    VERIFIED = "verified"
    REJECTED = "rejected"


class SourceKind(StrEnum):
    PUBLIC_WEBSITE = "public_website"
    PUBLIC_REGISTRY = "public_registry"
    PRESS_ARTICLE = "press_article"
    JOB_POSTING = "job_posting"
    DIRECTORY = "directory"
    MANUAL_IMPORT = "manual_import"
    PARTNER_REFERRAL = "partner_referral"


class ProspectStage(StrEnum):
    DISCOVERED = "discovered"
    QUALIFIED = "qualified"
    CONTACTED = "contacted"
    REPLIED = "replied"
    DEMO_SCHEDULED = "demo_scheduled"
    PILOT = "pilot"
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    BLOCKED = "blocked"


class ApprovalDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalSubject(StrEnum):
    """Objets qui ne peuvent pas franchir la frontière externe sans validation."""

    MESSAGE = "message"
    QUOTE = "quote"
    DISCOUNT = "discount"
    CONTRACT = "contract"
    LICENSE_ACTIVATION = "license_activation"
    INFRASTRUCTURE_CHANGE = "infrastructure_change"


class OutreachChannel(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    LINKEDIN = "linkedin"
    MEETING = "meeting"


class OutreachEventType(StrEnum):
    DRAFTED = "drafted"
    APPROVED = "approved"
    REJECTED = "rejected"
    SEND_BLOCKED = "send_blocked"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    OPENED = "opened"
    REPLIED = "replied"
    OPTED_OUT = "opted_out"
    DEMO_SCHEDULED = "demo_scheduled"
    DEMO_HELD = "demo_held"
    PILOT_STARTED = "pilot_started"
    DEAL_WON = "deal_won"
    DEAL_LOST = "deal_lost"


class SuppressionReason(StrEnum):
    OPT_OUT = "opt_out"
    BOUNCE_HARD = "bounce_hard"
    COMPLAINT = "complaint"
    LEGAL_REQUEST = "legal_request"
    MANUAL = "manual"
    COMPETITOR = "competitor"


class SuppressionScope(StrEnum):
    """Portée d'une opposition : un produit précis, ou toute l'organisation."""

    PRODUCT = "product"
    GLOBAL = "global"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentName(StrEnum):
    MARKET_SCOUT = "market_scout"
    LEAD_INTELLIGENCE = "lead_intelligence"
    OUTREACH_COMPOSER = "outreach_composer"
    CONVERSATION_ANALYST = "conversation_analyst"
    SALES_COORDINATOR = "sales_coordinator"
    PROPOSAL_ASSISTANT = "proposal_assistant"
    CUSTOMER_SUCCESS = "customer_success"
    GROWTH_ANALYST = "growth_analyst"


class PilotEventType(StrEnum):
    """Événements lus — jamais écrits — depuis Maintrix et EduCore."""

    PILOT_INVITED = "pilot.invited"
    PILOT_ACTIVATED = "pilot.activated"
    PILOT_FEEDBACK_RECEIVED = "pilot.feedback_received"
    LICENSE_STATUS_CHANGED = "license.status_changed"


class LegalBasis(StrEnum):
    """Base légale invoquée pour une prise de contact B2B."""

    LEGITIMATE_INTEREST = "legitimate_interest"
    CONSENT = "consent"
    EXISTING_RELATIONSHIP = "existing_relationship"
    UNDETERMINED = "undetermined"
