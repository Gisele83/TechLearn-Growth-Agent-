"""Énumérations métier partagées."""

from __future__ import annotations

from enum import StrEnum


class ProductCode(StrEnum):
    MAINTRIX = "maintrix"
    EDUCORE = "educore"


class Role(StrEnum):
    """Rôles applicatifs, du plus large au plus restreint.

    `operateur` existe pour une raison précise : produire et expédier n'est
    pas valider. Quelqu'un dont le travail est de relever les réponses et de
    reporter les oppositions a besoin d'écrire dans le système, pas
    d'approuver les messages des autres. Sans ce palier, le seul rôle
    capable de reporter une réponse emportait aussi le droit de valider un
    envoi — ce qui est précisément la décision qu'un second regard doit
    couvrir.
    """

    ADMIN = "admin"
    COMMERCIAL = "commercial"
    OPERATEUR = "operateur"
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


class OpportunityStage(StrEnum):
    """Étapes d'un dossier commercial.

    Distinctes de `ProspectStage`, qui décrit l'organisation : un prospect
    peut être « contacté » sans qu'aucun dossier ne soit ouvert, et un
    dossier perdu n'empêche pas de recontacter l'organisation l'an prochain.
    """

    OPEN = "open"
    DEMO = "demo"
    PROPOSAL = "proposal"
    PILOT = "pilot"
    WON = "won"
    LOST = "lost"


class NextActionKind(StrEnum):
    FOLLOW_UP = "follow_up"
    CALL = "call"
    DEMO = "demo"
    DEMO_REPORT = "demo_report"
    PROPOSAL = "proposal"
    RENEWAL = "renewal"
    #: Travail interne de clôture : ranger le dossier, prévenir le CRM.
    #: Ne s'adresse jamais au prospect, et reste donc permis après une
    #: opposition — c'est même la seule suite admissible.
    CLOSE_OUT = "close_out"


class NextActionStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class LossReason(StrEnum):
    """Motifs de perte, repris des objections classées.

    Fermés volontairement : un champ libre produit autant de motifs que de
    dossiers, et ne permet plus d'additionner quoi que ce soit.
    """

    NO_BUDGET = "no_budget"
    NO_NEED = "no_need"
    COMPETITOR = "competitor"
    TIMING = "timing"
    NO_RESPONSE = "no_response"
    NOT_DECISION_MAKER = "not_decision_maker"
    OTHER = "other"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    #: Remis à un fournisseur réel.
    SENT = "sent"
    #: Répétition : rien n'a quitté la machine. Distinct de SENT pour qu'une
    #: simulation ne puisse jamais être lue, comptée ni affichée comme un
    #: envoi réel.
    SENT_SIMULATED = "sent_simulated"
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


class EventSource(StrEnum):
    """D'où vient la certitude qu'un événement a eu lieu.

    La distinction compte pour lire un tableau de bord. « 12 démonstrations
    tenues » ne vaut pas la même chose selon que le système les a constatées
    ou qu'une personne les a saisies : la seconde dépend de la rigueur de
    quelqu'un, et son absence ne prouve rien.

    Tant qu'aucune intégration de réception n'existe, une réponse de prospect
    est nécessairement **déclarée** — personne ne l'a vue arriver.
    """

    #: Le système l'a constaté lui-même : un fournisseur a accepté un
    #: message, un garde-fou a bloqué un envoi, un connecteur produit a
    #: renvoyé un fait.
    OBSERVED = "observed"
    #: Une personne l'a saisi. Vrai jusqu'à preuve du contraire, mais sa
    #: présence dépend d'un geste humain, et son absence ne prouve rien.
    DECLARED = "declared"


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
    #: Rendez-vous manqué. Compté à part : un créneau perdu n'est ni une
    #: démonstration tenue, ni une absence de démonstration.
    DEMO_NO_SHOW = "demo_no_show"
    PILOT_STARTED = "pilot_started"
    DEAL_WON = "deal_won"
    DEAL_LOST = "deal_lost"


class DeliveryState(StrEnum):
    """Cycle de vie d'un envoi, du point de vue de ce qu'on sait réellement.

    `RESERVED` est écrit **avant** de remettre le message au fournisseur : la
    contrainte d'unicité sur la clé d'idempotence devient alors un verrou que
    deux processus concurrents ne peuvent pas franchir ensemble.

    `UNCERTAIN` est l'état le plus important : le fournisseur n'a ni accepté
    ni refusé de façon lisible (coupure, délai dépassé). On ignore si le
    message est parti. Il n'est donc ni rejoué ni effacé automatiquement —
    renvoyer un message peut-être déjà reçu est pire que de ne rien faire.
    """

    RESERVED = "reserved"
    SENT = "sent"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


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


#: Statuts signifiant « ce message est parti ». Un brouillon qui en porte un
#: n'est plus modifiable, plus approuvable, et plus renvoyable. Les deux y
#: figurent : une répétition n'atteint personne, mais le brouillon a servi et
#: le rejouer sèmerait la confusion sur ce qui a réellement été expédié.
SENT_STATUSES: frozenset[str] = frozenset({DraftStatus.SENT, DraftStatus.SENT_SIMULATED})
