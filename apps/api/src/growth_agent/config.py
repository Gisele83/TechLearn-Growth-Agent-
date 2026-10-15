"""Configuration de l'application.

Toute la configuration vient de l'environnement. Aucun secret n'a de valeur par
défaut : si une valeur sensible manque, le connecteur concerné reste en mode
`mock` et l'application le signale au démarrage plutôt que de tomber en panne
en production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class LegalRegime(StrEnum):
    """Régime de prospection applicable à un contact."""

    FR = "FR"
    CM = "CM"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    environment: Environment = Environment.LOCAL
    debug: bool = True
    api_host: str = "0.0.0.0"  # noqa: S104 - écoute conteneur, exposition pilotée par le reverse proxy
    api_port: int = 8000
    log_level: str = "INFO"
    secret_key: str = ""
    cors_origins: str = "http://localhost:5173"

    # --- Persistance ---------------------------------------------------------
    database_url: str = "sqlite+pysqlite:///./growth_agent.sqlite3"
    redis_url: str = "redis://localhost:6379/0"

    # --- CRM -----------------------------------------------------------------
    crm_provider: str = "mock"
    crm_base_url: str = "https://api.hubapi.com"
    crm_api_token: str = ""
    crm_rate_limit_per_second: float = 4.0
    crm_max_retries: int = 5

    # --- LLM -----------------------------------------------------------------
    llm_provider: str = "mock"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_max_input_tokens: int = 6000
    llm_max_output_tokens: int = 1200
    llm_daily_token_budget: int = 200_000

    # --- Recherche -----------------------------------------------------------
    search_provider: str = "mock"
    search_base_url: str = ""
    search_api_key: str = ""

    # --- Messagerie ----------------------------------------------------------
    email_provider: str = "mock"
    email_sending_enabled: bool = False
    email_from: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_user: str = ""
    email_smtp_password: str = ""
    email_max_per_contact_per_30_days: int = 2
    email_max_per_campaign_per_day: int = 50

    # --- Connecteurs produits ------------------------------------------------
    maintrix_adapter: str = "mock"
    maintrix_enabled: bool = False
    maintrix_base_url: str = ""
    maintrix_api_token: str = ""
    maintrix_webhook_secret: str = ""

    educore_adapter: str = "mock"
    educore_enabled: bool = False
    educore_base_url: str = ""
    educore_api_token: str = ""
    educore_webhook_secret: str = ""

    # --- Conformité ----------------------------------------------------------
    data_retention_days: int = 365
    default_legal_regime: LegalRegime = LegalRegime.FR

    # --- Purge des données ---------------------------------------------------
    # Durées de conservation par classe de donnée. Voir
    # docs/07-politique-de-retention.md pour la justification de chacune.
    draft_retention_days: int = 180
    audit_retention_days: int = 1095  # 3 ans : traçabilité des décisions
    pilot_event_retention_days: int = 1825  # 5 ans : suivi de la relation client

    # La purge doit être activée explicitement. Un système qui supprime des
    # données dès l'installation est un système dangereux.
    purge_enabled: bool = False

    # Sans `--apply`, la purge calcule et n'écrit rien. Le défaut est le mode
    # simulation, y compris quand la purge est activée.
    purge_dry_run_default: bool = True

    # Plafond absolu par exécution. Au-delà, l'exécution s'arrête : une purge
    # qui voudrait effacer des dizaines de milliers d'enregistrements signale
    # plus probablement une erreur de configuration qu'un vrai besoin.
    purge_max_records_per_run: int = 5000

    # Proportion maximale d'une table purgeable en une exécution. Un calcul de
    # date erroné rendrait soudain « échu » l'ensemble de la base ; ce garde-fou
    # arrête l'exécution avant la première suppression.
    purge_max_table_ratio: float = 0.5

    # Taille des lots. Chaque lot est validé séparément, ce qui permet à une
    # exécution interrompue de reprendre là où elle s'est arrêtée.
    purge_batch_size: int = 200

    # Au-delà de ce délai, une exécution encore « en cours » est considérée
    # comme interrompue et son verrou peut être repris.
    purge_lock_timeout_minutes: int = 60

    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, value: str) -> str:
        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @model_validator(mode="after")
    def _enforce_production_hardening(self) -> Settings:
        """Hors environnement local, refuser les configurations dangereuses."""
        if self.environment is Environment.LOCAL:
            return self
        if not self.secret_key or len(self.secret_key) < 32:
            raise ValueError(
                "GA_SECRET_KEY doit contenir au moins 32 caractères hors environnement local."
            )
        if self.debug:
            raise ValueError("GA_DEBUG doit être false hors environnement local.")
        if "*" in self.cors_origin_list:
            raise ValueError("GA_CORS_ORIGINS ne peut pas valoir '*' hors environnement local.")
        return self

    # --- Garde-fous d'activation --------------------------------------------
    def product_adapter_active(self, product_code: str) -> bool:
        """Un adaptateur produit HTTP n'est actif que s'il est explicitement autorisé.

        Les deux conditions — `*_ENABLED=true` ET `*_ADAPTER=http` ET une URL de
        base renseignée — doivent être réunies. En l'absence de l'une d'elles on
        retombe sur le mock, ce qui garantit qu'aucun appel n'est jamais émis
        vers Maintrix ou EduCore par accident.
        """
        prefix = product_code.lower()
        if prefix == "maintrix":
            return (
                self.maintrix_enabled
                and self.maintrix_adapter == "http"
                and bool(self.maintrix_base_url)
            )
        if prefix == "educore":
            return (
                self.educore_enabled
                and self.educore_adapter == "http"
                and bool(self.educore_base_url)
            )
        return False

    def can_send_email(self) -> bool:
        """L'envoi réel requiert le flag ET un fournisseur non-mock ET un expéditeur."""
        return (
            self.email_sending_enabled and self.email_provider != "mock" and bool(self.email_from)
        )

    def configuration_warnings(self) -> list[str]:
        """Avertissements affichés au démarrage, sans jamais révéler de secret."""
        warnings: list[str] = []
        if self.crm_provider != "mock" and not self.crm_api_token:
            warnings.append("CRM configuré en mode réel sans jeton : repli sur l'adaptateur mock.")
        if self.llm_provider != "mock" and not (self.llm_base_url and self.llm_api_key):
            warnings.append("LLM configuré en mode réel sans URL/clé : repli sur le mock.")
        if self.search_provider != "mock" and not self.search_base_url:
            warnings.append("Recherche configurée en mode réel sans URL : repli sur le mock.")
        if self.email_sending_enabled and self.email_provider == "mock":
            warnings.append(
                "GA_EMAIL_SENDING_ENABLED=true mais le fournisseur est 'mock' : aucun envoi réel."
            )
        for product in ("maintrix", "educore"):
            enabled = getattr(self, f"{product}_enabled")
            if enabled and not self.product_adapter_active(product):
                warnings.append(
                    f"Connecteur {product} marqué actif mais incomplet : repli sur le mock."
                )
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Utilisé par les tests pour recharger la configuration."""
    get_settings.cache_clear()


DEFAULT_PRODUCTS: tuple[dict[str, str], ...] = (
    {
        "code": "maintrix",
        "name": "Maintrix",
        "sector": "Maintenance et opérations industrielles",
        "description": "Diagnostic et GMAO pour ports, usines, agroalimentaire et énergie.",
    },
    {
        "code": "educore",
        "name": "EduCore",
        "sector": "Gestion scolaire et formation",
        "description": (
            "Gestion administrative pour écoles, lycées, centres de formation et universités."
        ),
    },
)
