"""aisstream.io websocket client with automatic reconnection."""

import asyncio
import random
from datetime import UTC, datetime

import orjson
import structlog
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from uatu.recorder.config import RecorderSettings
from uatu.recorder.messages import InvalidMessage, parse_message
from uatu.recorder.state import Session, Stats
from uatu.recorder.writer import DbWriter, RawArchive

log = structlog.get_logger(component="stream")


class AisStreamError(Exception):
    """aisstream.io sent an error message (e.g. invalid API key)."""


class StaleStreamError(Exception):
    """The connection is open but no messages are arriving."""


EXPECTED_DISCONNECTS = (ConnectionClosed, InvalidHandshake, OSError, TimeoutError, AisStreamError, StaleStreamError)


class AisStream:
    def __init__(
        self, settings: RecorderSettings, db_writer: DbWriter, archive: RawArchive | None, stats: Stats
    ) -> None:
        self._settings = settings
        self._db = db_writer
        self._archive = archive
        self._stats = stats
        self._subscription = orjson.dumps(
            {
                "APIKey": settings.aisstream_api_key.get_secret_value(),
                "BoundingBoxes": settings.bounding_boxes,
            }
        ).decode()

    async def run(self) -> None:
        """Stream forever, reconnecting with exponential backoff and jitter."""
        initial, maximum = self._settings.backoff_initial_s, self._settings.backoff_max_s
        delay = initial
        while True:
            session = await self._connect_and_consume()
            if session is not None and session.messages > 0:
                delay = initial  # the connection worked; start backing off from scratch
            wait = delay * random.uniform(0.5, 1.0)
            log.info("reconnecting", in_s=round(wait, 1))
            await asyncio.sleep(wait)
            delay = min(delay * 2, maximum)

    async def _connect_and_consume(self) -> Session | None:
        session: Session | None = None
        try:
            async with connect(self._settings.aisstream_url, max_size=2**22) as ws:
                # aisstream.io drops connections that do not subscribe within 3 seconds.
                await ws.send(self._subscription)
                session = Session()
                self._db.track(session)
                log.info("stream_connected", session_id=str(session.id))
                await self._consume(ws, session)
        except asyncio.CancelledError:
            if session is not None:
                session.close("shutdown")
            raise
        except Exception as e:
            reason = repr(e)
            log.warning(
                "stream_disconnected",
                reason=reason,
                session_id=str(session.id) if session else None,
                messages=session.messages if session else 0,
                exc_info=not isinstance(e, EXPECTED_DISCONNECTS),
            )
            if session is not None:
                session.close(reason)
            self._stats.reconnects += 1
        return session

    async def _consume(self, ws: ClientConnection, session: Session) -> None:
        timeout = self._settings.stale_timeout_s
        while True:
            try:
                async with asyncio.timeout(timeout):
                    raw = await ws.recv(decode=False)
            except TimeoutError:
                raise StaleStreamError(f"no messages for {timeout:.0f}s") from None
            self._handle(raw, session)

    def _handle(self, raw: bytes, session: Session) -> None:
        try:
            msg = orjson.loads(raw)
        except orjson.JSONDecodeError:
            self._stats.invalid += 1
            return
        if not isinstance(msg, dict):
            self._stats.invalid += 1
            return
        if "error" in msg:
            raise AisStreamError(str(msg["error"]))

        now = datetime.now(UTC)
        session.on_message(now)
        self._stats.received += 1

        if self._archive is not None:
            line = raw if b"\n" not in raw else orjson.dumps(msg)
            if not self._archive.offer((now, line)):
                self._stats.dropped_archive += 1

        try:
            rows = parse_message(msg)
        except InvalidMessage as e:
            self._stats.invalid += 1
            log.debug("invalid_message", error=str(e))
            return
        if not rows:
            self._stats.ignored += 1
        for row in rows:
            if not self._db.offer(row):
                self._stats.dropped_db += 1
