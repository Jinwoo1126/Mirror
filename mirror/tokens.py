"""Personal API tokens: generate, hash, and resolve a presented token to a user.

A token looks like `mir_<43 random url-safe chars>`. Only its SHA-256 hash is stored.
Resolving runs on the server's own database role (not as the user), since the user is
exactly what we are trying to find out.
"""

from __future__ import annotations

import hashlib
import secrets

PREFIX = "mir_"


def generate() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def looks_like_token(value: str) -> bool:
    return value.startswith(PREFIX) and 20 < len(value) < 100


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def prefix(raw: str) -> str:
    """The part safe to show in a list so people can tell tokens apart."""
    return raw[: len(PREFIX) + 4]


def resolve(dsn: str, raw: str) -> str | None:
    """Return the user id a live token belongs to, or None. Records last use at most
    once a minute so a busy hook does not write on every call."""
    import psycopg

    from . import schema

    if not looks_like_token(raw):
        return None
    now = schema.utcnow_iso()
    with psycopg.connect(dsn, autocommit=True) as c:
        row = c.execute(
            "select user_id, id, last_used_at from public.api_tokens "
            "where token_hash = %s and revoked_at is null", (digest(raw),)).fetchone()
        if row is None:
            return None
        uid, tid, last = row
        if not last or last[:16] != now[:16]:
            c.execute("update public.api_tokens set last_used_at = %s "
                      "where user_id = %s and id = %s", (now, uid, tid))
    return str(uid)
