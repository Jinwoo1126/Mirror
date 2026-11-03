"""Verify Supabase access tokens (JWTs) and turn them into a user id.

Two key styles, both handled:
- Asymmetric signing keys: set SUPABASE_URL; keys come from
  <SUPABASE_URL>/auth/v1/.well-known/jwks.json and are cached.
- Legacy shared secret: set SUPABASE_JWT_SECRET (HS256).

A token is accepted only if its signature, expiry and audience ("authenticated") check
out. Nothing here trusts a user id the client sent in a header or body.
"""

from __future__ import annotations

import os
import uuid

import jwt


class AuthError(Exception):
    """Missing, malformed, expired or forged token."""


_jwks_client: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        base = os.environ["SUPABASE_URL"].rstrip("/")
        _jwks_client = jwt.PyJWKClient(f"{base}/auth/v1/.well-known/jwks.json",
                                       cache_keys=True, lifespan=3600)
    return _jwks_client


def verify_token(token: str) -> str:
    """Return the user id (uuid string) the token was issued to."""
    return verify_claims(token)["sub"]


def verify_claims(token: str) -> dict:
    """Check the token and return its claims, with `sub` normalised to a user id string."""
    if not token:
        raise AuthError("missing token")
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")
        if alg == "HS256":
            if not secret:
                raise AuthError("HS256 token but no shared secret configured")
            key, algs = secret, ["HS256"]
        elif alg in ("RS256", "ES256", "EdDSA") and os.environ.get("SUPABASE_URL"):
            key, algs = _jwks().get_signing_key_from_jwt(token).key, [alg]
        else:
            raise AuthError(f"unsupported token algorithm: {alg or 'none'}")
        claims = jwt.decode(token, key, algorithms=algs, audience="authenticated",
                            options={"require": ["exp", "sub"]})
    except AuthError:
        raise
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e}") from e
    try:
        claims["sub"] = str(uuid.UUID(str(claims["sub"])))
    except (ValueError, KeyError) as e:
        raise AuthError("token subject is not a user id") from e
    return claims


def bearer(header: str | None) -> str:
    """Pull the token out of an Authorization header."""
    if not header:
        return ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""
