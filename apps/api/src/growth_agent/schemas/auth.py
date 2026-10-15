"""Schémas d'authentification."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    # « bearer » est le type de jeton OAuth 2.0, pas un secret.
    token_type: str = "bearer"  # noqa: S105
    expires_in_minutes: int
    role: str
    email: str


class CurrentUser(BaseModel):
    email: str
    role: str
    full_name: str = ""
