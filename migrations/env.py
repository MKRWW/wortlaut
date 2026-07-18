"""Alembic-Umgebung (async). URL + script_location werden programmatisch gesetzt
(siehe wortlaut.store.migrations)."""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
target_metadata = None  # 0001 nutzt rohes SQL, keine Autogenerierung


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None, "sqlalchemy.url muss gesetzt sein"
    connectable = create_async_engine(url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    raise NotImplementedError("offline mode wird nicht unterstützt")

asyncio.run(run_async_migrations())
