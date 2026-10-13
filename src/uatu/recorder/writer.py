"""Background writers: buffer rows in bounded queues and persist them in batches."""

import asyncio
import gzip
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import func, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from uatu import db
from uatu.db.models import IngestSession, Position, StaticReport, Vessel
from uatu.recorder.messages import PositionRow, Row, StaticRow
from uatu.recorder.state import Session, Stats

log = structlog.get_logger(component="writer")


class BatchWorker[T]:
    """Drains a bounded queue on a background task, in batches of batch_size items or flush_interval_s."""

    name = "worker"

    def __init__(self, *, batch_size: int, flush_interval_s: float, queue_max: int) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=queue_max)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._closing = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._waiting: asyncio.Timeout | None = None
        self._in_flight = 0

    @property
    def task(self) -> asyncio.Task[None]:
        assert self._task is not None, "worker not started"
        return self._task

    def offer(self, item: T) -> bool:
        """Queue an item without blocking; False if the buffer is full and the item was dropped."""
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            return False
        return True

    def qsize(self) -> int:
        return self._queue.qsize()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name=self.name)

    async def close(self, timeout_s: float) -> None:
        """Process everything still queued, giving up after timeout_s."""
        self._closing.set()
        if self._waiting is not None and not self._waiting.expired():  # stop waiting for the batch to fill
            self._waiting.reschedule(asyncio.get_running_loop().time())
        if self.task.done():
            return
        try:
            await asyncio.wait_for(self.task, timeout_s)
        except TimeoutError:
            log.error("shutdown_flush_timeout", worker=self.name, lost=self.qsize() + self._in_flight)

    async def _run(self) -> None:
        while not (self._closing.is_set() and self._queue.empty() and self._idle()):
            batch = await self._collect()
            self._in_flight = len(batch)
            await self._process(batch)
            self._in_flight = 0

    async def _collect(self) -> list[T]:
        """Up to batch_size items, waiting at most flush_interval_s (and not at all once closing) for them."""
        batch: list[T] = []
        if not self._closing.is_set():
            try:
                async with asyncio.timeout(self._flush_interval_s) as self._waiting:
                    while len(batch) < self._batch_size:
                        batch.append(await self._queue.get())
            except TimeoutError:
                pass
            finally:
                self._waiting = None
        while len(batch) < self._batch_size and not self._queue.empty():
            batch.append(self._queue.get_nowait())
        return batch

    def _idle(self) -> bool:
        """False while there is pending work outside the queue."""
        return True

    async def _process(self, batch: list[T]) -> None:
        raise NotImplementedError


VESSEL_FIELDS = (
    "name", "callsign", "imo", "ship_type", "dim_a", "dim_b", "dim_c", "dim_d", "draught", "destination"
)  # fmt: skip


def merge_vessels(reports: Iterable[StaticRow]) -> list[dict[str, Any]]:
    """Collapse static reports into one `vessels` row per MMSI; later non-null values win."""
    merged: dict[int, dict[str, Any]] = {}
    for r in reports:
        row = merged.get(r.mmsi)
        if row is None:
            row = merged[r.mmsi] = {
                "mmsi": r.mmsi,
                **dict.fromkeys(VESSEL_FIELDS),
                "first_seen_at": r.received_at,
                "last_static_at": r.received_at,
            }
        row["ais_class"] = "A" if r.msg_type == 5 else "B"
        row["first_seen_at"] = min(row["first_seen_at"], r.received_at)
        row["last_static_at"] = max(row["last_static_at"], r.received_at)
        for f in VESSEL_FIELDS:
            if (value := getattr(r, f)) is not None:
                row[f] = value
    return list(merged.values())


def _upsert_vessels():
    stmt = pg_insert(Vessel)
    new, old = stmt.excluded, Vessel.__table__.c
    return stmt.on_conflict_do_update(
        index_elements=[Vessel.mmsi],
        set_={
            "ais_class": new.ais_class,
            **{f: func.coalesce(new[f], old[f]) for f in VESSEL_FIELDS},
            "first_seen_at": func.least(old.first_seen_at, new.first_seen_at),
            "last_static_at": func.greatest(old.last_static_at, new.last_static_at),
        },
    )


def _upsert_sessions():
    stmt = pg_insert(IngestSession)
    return stmt.on_conflict_do_update(
        index_elements=[IngestSession.id],
        set_={c: stmt.excluded[c] for c in ("last_message_at", "disconnected_at", "messages", "close_reason")},
    )


class DbWriter(BatchWorker[Row]):
    """Writes positions, static reports, vessels and ingest sessions; retries while the database is down."""

    name = "db"

    def __init__(self, engine: AsyncEngine, stats: Stats, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._engine = engine
        self._stats = stats
        self._sessions: list[Session] = []
        self._upsert_vessels = _upsert_vessels()
        self._upsert_sessions = _upsert_sessions()

    def track(self, session: Session) -> None:
        """Persist this session's progress on every flush until it is closed."""
        self._sessions.append(session)

    def _idle(self) -> bool:
        return not self._sessions

    async def _process(self, batch: list[Row]) -> None:
        if not batch and not self._sessions:
            return
        delay = 1.0
        while True:
            try:
                await self._flush(batch)
                return
            except (SQLAlchemyError, OSError, TimeoutError) as e:
                if not db.is_transient(e):
                    log.error("db_batch_rejected", error=repr(e), rows=len(batch))
                    self._stats.dropped_db += len(batch)
                    self._sessions = [s for s in self._sessions if s.disconnected_at is None]
                    return
                log.warning("db_write_failed", error=repr(e), rows=len(batch), queued=self.qsize(), retry_in_s=delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def _flush(self, batch: list[Row]) -> None:
        positions = [r._asdict() for r in batch if isinstance(r, PositionRow)]
        statics = [r for r in batch if isinstance(r, StaticRow)]
        sessions = [asdict(s) for s in self._sessions]

        async with self._engine.begin() as conn:
            if positions:
                await conn.execute(insert(Position), positions)
            if statics:
                await conn.execute(insert(StaticReport), [r._asdict() for r in statics])
                await conn.execute(self._upsert_vessels, merge_vessels(statics))
            if sessions:
                await conn.execute(self._upsert_sessions, sessions)

        self._stats.positions_written += len(positions)
        self._stats.statics_written += len(statics)
        closed = {s["id"] for s in sessions if s["disconnected_at"] is not None}
        self._sessions = [s for s in self._sessions if s.id not in closed]


class RawArchive(BatchWorker[tuple[datetime, bytes]]):
    """Appends every raw message to hourly gzipped JSON Lines files: root/YYYY/MM/DD/YYYYMMDDTHH.jsonl.gz.

    The parsed tables only keep what Uatu needs today; this keeps everything for reprocessing.
    """

    name = "archive"

    def __init__(self, root: Path, stats: Stats, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._root = root
        self._stats = stats

    async def _process(self, batch: list[tuple[datetime, bytes]]) -> None:
        if not batch:
            return
        try:
            await asyncio.to_thread(self._write, batch)
        except OSError as e:
            log.error("archive_write_failed", error=repr(e), lines=len(batch))
            self._stats.dropped_archive += len(batch)

    def _write(self, batch: list[tuple[datetime, bytes]]) -> None:
        by_file: defaultdict[str, list[bytes]] = defaultdict(list)
        for received_at, line in batch:
            by_file[received_at.strftime("%Y/%m/%d/%Y%m%dT%H.jsonl.gz")].append(line)
        for relative, lines in by_file.items():
            path = self._root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            # Each append adds a complete gzip member, so a crash never corrupts earlier data.
            with gzip.open(path, "ab") as f:
                f.write(b"\n".join(lines) + b"\n")
