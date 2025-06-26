# migrations/env.py
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context

from bot.config import get_settings
from bot.db import Base

# this is the Alembic Config object
config = context.config
fileConfig(config.config_file_name)

# add your model’s MetaData object here
target_metadata = Base.metadata

# override URL from settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_sync_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # you can add version_table_schema, etc. here
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online():
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.begin() as connection:
        await connection.run_sync(run_sync_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    # fallback to standard offline migrations
    context.run_migrations()
else:
    asyncio.run(run_migrations_online())
