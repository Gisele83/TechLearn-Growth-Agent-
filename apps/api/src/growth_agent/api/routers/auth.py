"""Authentification."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from growth_agent.api.deps import CurrentUserDep, SessionDep, SettingsDep, _signing_secret
from growth_agent.models.ops import User
from growth_agent.schemas.auth import CurrentUser, LoginRequest, TokenResponse
from growth_agent.security.passwords import verify_password
from growth_agent.security.tokens import DEFAULT_TTL_MINUTES, create_access_token
from growth_agent.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["authentification"])


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    user = session.execute(
        select(User).where(User.email == payload.email.lower())
    ).scalar_one_or_none()

    # Message identique dans les deux cas : ne pas révéler si l'adresse existe.
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        record_audit(
            session,
            actor=payload.email.lower(),
            action="auth.login_failed",
            object_type="user",
            outcome="failure",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants invalides.",
        )

    user.last_login_at = datetime.now(UTC)
    token = create_access_token(
        subject=user.email, role=user.role, secret=_signing_secret(settings)
    )
    record_audit(
        session,
        actor=user.email,
        actor_role=user.role,
        action="auth.login",
        object_type="user",
        object_id=user.id,
    )
    return TokenResponse(
        access_token=token,
        expires_in_minutes=DEFAULT_TTL_MINUTES,
        role=user.role,
        email=user.email,
    )


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUserDep) -> CurrentUser:
    return user
