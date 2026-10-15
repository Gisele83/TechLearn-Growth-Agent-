"""Dépendances FastAPI : session, utilisateur courant, rôles, connecteurs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from growth_agent.config import Settings, get_settings
from growth_agent.connectors.crm.base import CRMAdapter
from growth_agent.connectors.crm.factory import get_crm_adapter
from growth_agent.connectors.email.base import EmailProvider
from growth_agent.connectors.email.factory import get_email_provider
from growth_agent.connectors.llm.base import LLMProvider
from growth_agent.connectors.llm.factory import get_llm_provider
from growth_agent.connectors.search.base import SearchProvider
from growth_agent.connectors.search.factory import get_search_provider
from growth_agent.db.session import get_session
from growth_agent.models.catalog import Product
from growth_agent.models.ops import User
from growth_agent.schemas.auth import CurrentUser
from growth_agent.security.rbac import PermissionDenied, has_role
from growth_agent.security.tokens import TokenError, decode_access_token


def db_session() -> Iterator[Session]:
    yield from get_session()


SessionDep = Annotated[Session, Depends(db_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def current_user(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'accès absent.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token, secret=_signing_secret(settings))
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    email = str(payload.get("sub", ""))
    user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte inconnu ou désactivé."
        )
    # Le rôle fait foi en base, pas dans le jeton : une rétrogradation prend
    # effet immédiatement, sans attendre l'expiration du jeton.
    return CurrentUser(email=user.email, role=user.role, full_name=user.full_name)


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]


def require(role: str):
    """Fabrique une dépendance exigeant au moins `role`."""

    def _dependency(user: CurrentUserDep) -> CurrentUser:
        if not has_role(user.role, role):
            raise PermissionDenied(role, user.role)
        return user

    return _dependency


def crm_adapter(settings: SettingsDep) -> CRMAdapter:
    return get_crm_adapter(settings)


def llm_provider(settings: SettingsDep) -> LLMProvider:
    return get_llm_provider(settings)


def search_provider(settings: SettingsDep) -> SearchProvider:
    return get_search_provider(settings)


def email_provider(settings: SettingsDep) -> EmailProvider:
    return get_email_provider(settings)


CRMDep = Annotated[CRMAdapter, Depends(crm_adapter)]
LLMDep = Annotated[LLMProvider, Depends(llm_provider)]
SearchDep = Annotated[SearchProvider, Depends(search_provider)]
EmailDep = Annotated[EmailProvider, Depends(email_provider)]


def resolve_product(session: Session, product_code: str) -> Product:
    product = session.execute(
        select(Product).where(Product.code == product_code.lower())
    ).scalar_one_or_none()
    if product is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produit « {product_code} » inconnu.",
        )
    return product


def _signing_secret(settings: Settings) -> str:
    """Clé de signature.

    En local, une clé de développement fixe évite d'invalider les sessions à
    chaque redémarrage. Hors local, la configuration impose déjà une vraie clé
    (validée dans `Settings`), donc ce repli ne peut pas s'appliquer.
    """
    if settings.secret_key:
        return settings.secret_key
    return "cle-de-developpement-local-non-utilisable-en-production"
