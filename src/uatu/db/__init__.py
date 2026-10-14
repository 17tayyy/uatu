"""Database access shared by every Uatu component. The schema lives in models.py, migrations in Alembic."""

import asyncio

import structlog
from sqlalchemy import exc, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

log = structlog.get_logger(component="db")


def create_engine(url: str, *, pool_size: int = 2, statement_timeout_ms: int | None = None) -> AsyncEngine:
    connect_args = {}
    if statement_timeout_ms is not None:
        connect_args["server_settings"] = {"statement_timeout": str(statement_timeout_ms)}
    return create_async_engine(url, pool_size=pool_size, max_overflow=0, pool_pre_ping=True, connect_args=connect_args)


def is_transient(error: BaseException) -> bool:
    """True if the error means "the database is unreachable right now" rather than "this data is bad"."""
    if isinstance(error, OSError | TimeoutError | exc.OperationalError):
        return True
    return isinstance(error, exc.DBAPIError) and error.connection_invalidated


async def wait_until_ready(engine: AsyncEngine, *, retry_max_s: float = 30.0) -> str:
    """Wait until the database accepts connections, then return its Alembic schema revision."""
    delay = 1.0
    while True:
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                return result.scalar_one()
        except exc.ProgrammingError as e:
            raise RuntimeError("database schema not initialised: run `alembic upgrade head`") from e
        except Exception as e:
            if not is_transient(e):
                raise
            log.warning("db_unavailable", error=repr(e), retry_in_s=delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, retry_max_s)
