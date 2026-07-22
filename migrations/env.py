"""Alembic environment for llm-gateway.

The URL is taken from GATEWAY_DATABASE_URL — the same variable the app reads — so
migrations and the running service always point at the same database, and no
connection string is committed. `alembic upgrade head` is what a real deploy runs
in place of the store's convenience create_all().
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from llmgateway.store import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Let the environment supply the URL that alembic.ini deliberately omits.
_url = os.environ.get("GATEWAY_DATABASE_URL")
if _url:
    config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
