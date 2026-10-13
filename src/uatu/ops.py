"""Keeping Uatu running unattended: database backups, health checks and alerts."""

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu.db.models import IngestSession

log = structlog.get_logger(component="ops")


# --- backups -------------------------------------------------------------------------------------


async def backup_database(database_url: str, dest_dir: Path, keep: int) -> Path:
    """pg_dump the database (custom format) into dest_dir; keep only the `keep` newest dumps."""
    url = make_url(database_url)
    await asyncio.to_thread(dest_dir.mkdir, parents=True, exist_ok=True)
    path = dest_dir / f"uatu-{datetime.now(UTC):%Y%m%dT%H%MZ}.dump"
    partial = path.with_name(path.name + ".part")  # a dump cut short never passes for a good one
    process = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--format=custom",
        f"--file={partial}",
        f"--host={url.host}",
        f"--port={url.port or 5432}",
        f"--username={url.username}",
        url.database or "",
        env={**os.environ, "PGPASSWORD": url.password or ""},
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump exited with {process.returncode}: {stderr.decode().strip()[-500:]}")
    await asyncio.to_thread(_keep_newest, partial, path, keep)
    return path


def _keep_newest(partial: Path, path: Path, keep: int) -> None:
    partial.replace(path)
    for old in to_prune(list(path.parent.glob("uatu-*.dump")), keep):
        old.unlink()


def to_prune(dumps: list[Path], keep: int) -> list[Path]:
    """The dumps beyond the `keep` newest (names sort chronologically)."""
    return sorted(dumps)[:-keep] if len(dumps) > keep else []


# --- health checks --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str


async def check_ais(engine: AsyncEngine, max_age: timedelta) -> Check:
    """The recorder updates its session every couple of seconds while messages arrive."""
    async with engine.connect() as conn:
        last = await conn.scalar(select(func.max(IngestSession.last_message_at)))
    if last is None:
        return Check("ais", False, "no AIS message ever recorded")
    age = datetime.now(UTC) - last
    return Check("ais", age <= max_age, f"last AIS message {age.total_seconds() / 60:.0f} min ago")


def check_disk(path: Path, min_free: float) -> Check:
    usage = shutil.disk_usage(path)
    free = usage.free / usage.total
    return Check("disk", free >= min_free, f"{free:.0%} free ({usage.free / 2**30:.0f} GB) on {path}")


def check_backup(dest_dir: Path, max_age: timedelta) -> Check:
    dumps = sorted(dest_dir.glob("uatu-*.dump")) if dest_dir.exists() else []
    if not dumps:
        return Check("backup", True, "no backup yet")  # a failing backup task alerts by itself
    age = datetime.now(UTC) - datetime.fromtimestamp(dumps[-1].stat().st_mtime, UTC)
    return Check("backup", age <= max_age, f"last backup {age.total_seconds() / 3600:.0f} h ago ({dumps[-1].name})")


# --- alerts ---------------------------------------------------------------------------------------

type Send = Callable[[str, str, str], Awaitable[None]]  # title, message, priority


class Alerter:
    """Notifies when a check starts failing and when it recovers, rather than on every run."""

    def __init__(self, send: Send) -> None:
        self._send = send
        self._failing: dict[str, str] = {}

    async def update(self, checks: list[Check]) -> None:
        for check in checks:
            if not check.ok and check.name not in self._failing:
                self._failing[check.name] = check.detail
                await self.notify(f"Uatu: {check.name} failing", check.detail, "high")
            elif check.ok and check.name in self._failing:
                del self._failing[check.name]
                await self.notify(f"Uatu: {check.name} recovered", check.detail, "default")

    async def notify(self, title: str, message: str, priority: str = "default") -> None:
        log.warning("alert", title=title, message=message)
        try:
            await self._send(title, message, priority)
        except httpx.HTTPError as e:  # alerting must never take the worker down
            log.error("alert_not_sent", error=repr(e))


def ntfy(http: httpx.AsyncClient, topic_url: str | None) -> Send:
    """Push notifications through ntfy (https://ntfy.sh): subscribe to the topic in its app."""

    async def send(title: str, message: str, priority: str) -> None:
        if topic_url is None:
            return
        response = await http.post(topic_url, content=message.encode(), headers={"Title": title, "Priority": priority})
        response.raise_for_status()

    return send


async def ping_healthcheck(http: httpx.AsyncClient, url: str, ok: bool) -> None:
    """Dead man's switch (e.g. healthchecks.io): it alerts when pings stop, i.e. when the worker,
    the container or the homelab itself is down."""
    try:
        (await http.get(url if ok else f"{url}/fail")).raise_for_status()
    except httpx.HTTPError as e:
        log.error("healthcheck_ping_failed", error=repr(e))
