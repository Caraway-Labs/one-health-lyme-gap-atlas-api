"""Supabase access-token verification for private Atlas API routes."""

from typing import Protocol
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from jwt import PyJWKClient

from .config import ApiSettings


class AuthenticatedUser(Protocol):
    """Minimum stable identity exposed to private route handlers."""

    @property
    def user_id(self) -> UUID: ...


class SupabaseAuthenticatedUser:
    def __init__(self, user_id: UUID) -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> UUID:
        return self._user_id


class TokenVerifier(Protocol):
    def verify(self, authorization: str | None) -> AuthenticatedUser: ...


class SupabaseTokenVerifier:
    """Validate bearer tokens with Supabase's rotating public JWKS."""

    def __init__(self, settings: ApiSettings) -> None:
        self._issuer = settings.supabase_jwt_issuer
        self._audience = settings.supabase_jwt_audience
        self._jwks = PyJWKClient(f"{self._issuer}/.well-known/jwks.json")

    def verify(self, authorization: str | None) -> AuthenticatedUser:
        if not authorization or not authorization.startswith("Bearer "):
            raise self._unauthorized()
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise self._unauthorized()
        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256", "EdDSA"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iat", "sub"]},
            )
            if claims.get("role") != "authenticated":
                raise self._unauthorized()
            return SupabaseAuthenticatedUser(UUID(str(claims["sub"])))
        except (jwt.PyJWTError, ValueError, OSError) as exc:
            if isinstance(exc, HTTPException):
                raise exc
            raise self._unauthorized() from exc

    @staticmethod
    def _unauthorized() -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid authenticated session is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
