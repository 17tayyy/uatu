"""Signing in to the dashboard: a few users at most, a session per browser.

The session cookie carries a random token; the database keeps only its SHA-256, so a copy of the
database signs no one in. Sessions last SESSION_DAYS, or until signing out or a new password.
"""

import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import structlog
from pwdlib import PasswordHash
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from uatu.db.models import DashboardSession, DashboardUser
from uatu.web.errors import UnauthorizedError

log = structlog.get_logger(component="web")

SESSION_DAYS = 30
password_hash = PasswordHash.recommended()  # argon2id
# Checked against when the user does not exist, so that the answer takes as long either way.
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(16))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def sign_in(db: AsyncSession, username: str, password: str) -> tuple[DashboardUser, str, datetime]:
    """(user, session token for the cookie, when it expires); the same error for a wrong name or password."""
    user = await db.scalar(select(DashboardUser).where(DashboardUser.username == username))
    valid, new_hash = await asyncio.to_thread(
        password_hash.verify_and_update, password, user.password_hash if user else DUMMY_HASH
    )
    if user is None or not valid:
        log.warning("auth.login_failed", reason="unknown_user" if user is None else "wrong_password")
        raise UnauthorizedError("Wrong username or password")
    if new_hash:
        user.password_hash = new_hash
    now = datetime.now(UTC)
    token, expires = secrets.token_urlsafe(32), now + timedelta(days=SESSION_DAYS)
    db.add(DashboardSession(token_hash=token_hash(token), user_id=user.id, expires_at=expires))
    await db.execute(delete(DashboardSession).where(DashboardSession.expires_at < now))
    await db.commit()
    log.info("auth.login", user_id=user.id)
    return user, token, expires


async def user_for(db: AsyncSession, token: str) -> DashboardUser | None:
    """Whose live session this token is, if any."""
    return await db.scalar(
        select(DashboardUser)
        .join(DashboardSession, DashboardSession.user_id == DashboardUser.id)
        .where(DashboardSession.token_hash == token_hash(token), DashboardSession.expires_at > datetime.now(UTC))
    )


async def sign_out(db: AsyncSession, token: str | None) -> None:
    if token:
        await db.execute(delete(DashboardSession).where(DashboardSession.token_hash == token_hash(token)))
        await db.commit()


async def set_password(db: AsyncSession, username: str, password: str) -> bool:
    """Create the user, or give it a new password and sign it out everywhere. True if created."""
    hashed = await asyncio.to_thread(password_hash.hash, password)
    user = await db.scalar(select(DashboardUser).where(DashboardUser.username == username))
    if user is None:
        db.add(DashboardUser(username=username, password_hash=hashed))
    else:
        user.password_hash = hashed
        await db.execute(delete(DashboardSession).where(DashboardSession.user_id == user.id))
    await db.commit()
    return user is None
