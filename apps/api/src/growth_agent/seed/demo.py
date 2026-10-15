"""Données de référence et jeu de démonstration.

`seed_reference_data` crée les deux produits — c'est le minimum vital, sûr dans
tous les environnements.

`seed_demo_data` ajoute profils cibles, campagnes, gabarits et comptes de
démonstration. Il REFUSE de s'exécuter en production : les mots de passe y sont
connus de tous et les organisations sont fictives.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import DEFAULT_PRODUCTS, Environment, Settings, get_settings
from growth_agent.models.catalog import Campaign, IdealCustomerProfile, Product
from growth_agent.models.enums import CampaignStatus, Role
from growth_agent.models.ops import User
from growth_agent.models.outreach import MessageTemplate
from growth_agent.security.passwords import hash_password


class SeedRefused(Exception):
    """Le jeu de démonstration ne peut pas être installé dans cet environnement."""


def seed_reference_data(session: Session) -> dict[str, Product]:
    """Crée les produits s'ils n'existent pas. Idempotent."""
    products: dict[str, Product] = {}
    for entry in DEFAULT_PRODUCTS:
        existing = session.execute(
            select(Product).where(Product.code == entry["code"])
        ).scalar_one_or_none()
        if existing is None:
            existing = Product(**entry)
            session.add(existing)
            session.flush()
        products[entry["code"]] = existing
    return products


DEMO_USERS: tuple[tuple[str, str, str, str], ...] = (
    ("admin@growth.example.org", "Administratrice démo", Role.ADMIN, "DemoAdmin!2026"),
    ("commercial@growth.example.org", "Commercial démo", Role.COMMERCIAL, "DemoCommercial!2026"),
    ("approbateur@growth.example.org", "Approbateur démo", Role.COMMERCIAL, "DemoApprobateur!2026"),
    ("lecteur@growth.example.org", "Lecteur démo", Role.LECTEUR, "DemoLecteur!2026"),
)


def seed_demo_data(session: Session, settings: Settings | None = None) -> dict[str, object]:
    settings = settings or get_settings()
    if settings.environment is Environment.PRODUCTION:
        raise SeedRefused(
            "Le jeu de démonstration contient des comptes à mots de passe connus : "
            "il est interdit en production."
        )

    products = seed_reference_data(session)
    created: dict[str, object] = {"products": len(products)}

    # --- Comptes ------------------------------------------------------------
    users_created = 0
    for email, full_name, role, password in DEMO_USERS:
        existing = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            User(
                email=email,
                full_name=full_name,
                role=role,
                password_hash=hash_password(password),
            )
        )
        users_created += 1
    session.flush()
    created["users"] = users_created

    # --- Profils cibles -----------------------------------------------------
    icps = {
        "maintrix": _ensure_icp(
            session,
            product=products["maintrix"],
            name="Industriels et prestataires de maintenance",
            description=(
                "Sites industriels et prestataires dont la maintenance est encore "
                "suivie sur tableur ou sur papier."
            ),
            country_codes=["CM", "FR"],
            target_sectors=[
                "Logistique portuaire",
                "Agroalimentaire",
                "Énergie",
                "Mécanique de précision",
            ],
            target_roles=["Responsable maintenance", "Directeur technique", "Responsable production"],
            min_headcount=50,
            max_headcount=2000,
            need_signals=["maintenance", "GMAO", "arrêts machine", "modernisation", "digitalis"],
            exclusion_signals=["éditeur de GMAO", "cabinet de conseil en maintenance"],
        ),
        "educore": _ensure_icp(
            session,
            product=products["educore"],
            name="Établissements scolaires et centres de formation",
            description=(
                "Établissements dont la gestion administrative reste manuelle et "
                "dont les effectifs augmentent."
            ),
            country_codes=["CM", "FR"],
            target_sectors=[
                "Enseignement secondaire",
                "Enseignement supérieur",
                "Formation professionnelle",
            ],
            target_roles=[
                "Chef d'établissement",
                "Directeur administratif",
                "Promoteur",
                "Secrétaire général",
            ],
            min_headcount=10,
            max_headcount=1000,
            need_signals=["gestion administrative", "inscriptions", "scolarité", "digitalis"],
            exclusion_signals=["éditeur de logiciel scolaire"],
        ),
    }
    created["icps"] = len(icps)

    # --- Campagnes ----------------------------------------------------------
    campaigns = 0
    for code, (slug, name, objective, regime) in {
        "maintrix": (
            "pilote-industrie-cm-2026",
            "Pilote industrie Cameroun 2026",
            "Recruter 3 à 5 pilotes sur des sites industriels au Cameroun.",
            "CM",
        ),
        "educore": (
            "pilote-etablissements-cm-2026",
            "Pilote établissements Cameroun 2026",
            "Recruter 5 à 8 établissements pilotes au Cameroun.",
            "CM",
        ),
    }.items():
        existing = session.execute(
            select(Campaign).where(
                Campaign.product_id == products[code].id, Campaign.slug == slug
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            Campaign(
                product_id=products[code].id,
                icp_id=icps[code].id,
                slug=slug,
                name=name,
                objective=objective,
                status=CampaignStatus.ACTIVE,
                legal_regime=regime,
                owner_email="commercial@growth.example.org",
                target_metrics=(
                    {"qualified_organizations": 50, "demos_held": 10, "pilots_started": 3}
                    if code == "maintrix"
                    else {"qualified_organizations": 100, "demos_held": 15, "pilots_started": 5}
                ),
            )
        )
        campaigns += 1
    session.flush()
    created["campaigns"] = campaigns

    # --- Gabarits -----------------------------------------------------------
    templates = 0
    for code, (slug, name, subject, body) in {
        "maintrix": (
            "premier-contact-industrie",
            "Premier contact — industrie",
            "{{product_name}} — démonstration de 30 minutes pour {{organization_name}}",
            (
                "Bonjour,\n\n"
                "Je m'adresse à vous en tant que {{contact_role}} de {{organization_name}}.\n"
                "{{need_line}}\n\n"
                "{{product_name}} permet de suivre les interventions et les arrêts machine "
                "sur un scénario vérifiable. Seriez-vous disponible pour une démonstration "
                "de 30 minutes ?\n\n"
                "Si ce message ne vous concerne pas, répondez « STOP » : votre adresse sera "
                "retirée de nos envois.\n\nBien cordialement,"
            ),
        ),
        "educore": (
            "premier-contact-etablissement",
            "Premier contact — établissement",
            "{{product_name}} — gestion administrative de {{organization_name}}",
            (
                "Bonjour,\n\n"
                "Je m'adresse à vous en tant que {{contact_role}} de {{organization_name}}.\n"
                "{{need_line}}\n\n"
                "{{product_name}} prend en charge inscriptions, scolarité et suivi "
                "administratif. Seriez-vous disponible pour une démonstration de 30 minutes "
                "sur un scénario de votre établissement ?\n\n"
                "Si ce message ne vous concerne pas, répondez « STOP » : votre adresse sera "
                "retirée de nos envois.\n\nBien cordialement,"
            ),
        ),
    }.items():
        existing = session.execute(
            select(MessageTemplate).where(
                MessageTemplate.product_id == products[code].id, MessageTemplate.slug == slug
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue
        session.add(
            MessageTemplate(
                product_id=products[code].id,
                slug=slug,
                name=name,
                subject_template=subject,
                body_template=body,
                allowed_variables=[
                    "product_name",
                    "organization_name",
                    "contact_role",
                    "need_line",
                ],
            )
        )
        templates += 1
    session.flush()
    created["templates"] = templates
    return created


def _ensure_icp(session: Session, *, product: Product, name: str, **kwargs) -> IdealCustomerProfile:
    existing = session.execute(
        select(IdealCustomerProfile).where(
            IdealCustomerProfile.product_id == product.id, IdealCustomerProfile.name == name
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    icp = IdealCustomerProfile(product_id=product.id, name=name, **kwargs)
    session.add(icp)
    session.flush()
    return icp
