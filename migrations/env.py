"""Alembic environment: migrates DATABASE_URL towards the schema declared in uatu.db.models."""

import asyncio
import logging

from alembic import context
from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from uatu.config import DatabaseSettings
from uatu.db.models import Base

logging.basicConfig(level=logging.INFO, format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("alembic.ddl.postgresql").setLevel(logging.WARNING)  # noisy about PostGIS tables


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    # Manage only the tables declared in models.py. The PostGIS image ships its own
    # (spatial_ref_sys, the tiger geocoder...) which must never be dropped.
    return not (type_ == "table" and reflected and compare_to is None)


CONFIGURE_OPTS = {
    "target_metadata": Base.metadata,
    "include_object": include_object,
    "render_item": alembic_helpers.render_item,  # emits geoalchemy2 types along with their import
}


def run_migrations_offline() -> None:
    context.configure(
        url=DatabaseSettings().database_url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **CONFIGURE_OPTS,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, **CONFIGURE_OPTS)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(DatabaseSettings().database_url, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
