"""Runtime state shared between the stream and the writers."""

import asyncio
import uuid
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Protocol

import structlog

log = structlog.get_logger(component="recorder")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Session:
    """One websocket connection to aisstream.io; mirrors a row of ingest_sessions."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    connected_at: datetime = field(default_factory=_now)
    last_message_at: datetime | None = None
    disconnected_at: datetime | None = None
    messages: int = 0
    close_reason: str | None = None

    def on_message(self, at: datetime) -> None:
        self.messages += 1
        self.last_message_at = at

    def close(self, reason: str) -> None:
        self.disconnected_at = _now()
        self.close_reason = reason


@dataclass(slots=True)
class Stats:
    received: int = 0
    ignored: int = 0  # message types Uatu does not store
    invalid: int = 0
    positions_written: int = 0
    statics_written: int = 0
    dropped_db: int = 0
    dropped_archive: int = 0
    reconnects: int = 0


class _Queued(Protocol):
    name: str

    def qsize(self) -> int: ...


async def report_stats(stats: Stats, workers: Iterable[_Queued], interval_s: float) -> None:
    """Log counters for the last interval, plus queue depths, every interval_s seconds."""
    previous = asdict(stats)
    while True:
        await asyncio.sleep(interval_s)
        current = asdict(stats)
        log.info(
            "stats",
            interval_s=interval_s,
            **{key: current[key] - previous[key] for key in current},
            **{f"{w.name}_queue": w.qsize() for w in workers},
        )
        previous = current
