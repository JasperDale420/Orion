import asyncio
from logging.config import fileConfig

from sqlalchemy import Column, MetaData, Table, Text, inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
import os  # noqa: E402
import sys  # noqa: E402

sys.path.append(os.path.join(os.getcwd(), "src"))

from orion.storage.models import Base  # noqa: E402

# Import every model module so all tables register on Base.metadata before
# autogenerate compares it against the database. models.py only defines its
# own tables; the remaining tables live in these sibling modules (the same set
# init_db() imports). Without these imports autogen sees a partial schema.
from orion.storage import (  # noqa: E402, F401
    models_audit,
    models_dlq,
    models_execution,
    models_gold,
    models_liveness,
    models_ml,
    models_rag,
    models_risk,
    models_signals,
    models_silver,
    models_solvers,
    models_trade_journal,
)

target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def _include_object(obj, name, type_, reflected, compare_to):
    """Autogenerate guard: never propose DROPs of database-only tables.

    The live DB can carry legacy tables that predate the 2026-06-11 baseline
    squash (e.g. historical artifacts with no model). A reflected table with
    no matching model (compare_to is None) would otherwise autogenerate a
    drop_table — silently destructive against production history. Additive
    drift (model exists, table missing) is still surfaced normally.
    """
    if type_ == "table" and reflected and compare_to is None:
        return False
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def _ensure_alembic_version_table(connection: Connection) -> None:
    inspector = inspect(connection)

    if not inspector.has_table("alembic_version"):
        md = MetaData()
        Table(
            "alembic_version",
            md,
            Column("version_num", Text(), primary_key=True, nullable=False),
        )
        md.create_all(connection)
        return

    columns = inspector.get_columns("alembic_version")
    version_num = next((c for c in columns if c.get("name") == "version_num"), None)
    if version_num is None:
        raise RuntimeError("alembic_version exists but version_num column is missing")

    col_type = version_num.get("type")
    # When Alembic creates this table, it defaults to VARCHAR(32) which is too small
    # for our revision IDs like '0002_event_envelope_and_trading_date'.
    if getattr(col_type, "length", None) == 32:
        connection.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE TEXT"))


def do_run_migrations(connection: Connection) -> None:
    # Ensure the version table is created/altered outside of Alembic's migration
    # transaction handling, otherwise it may be created within an uncommitted
    # transaction and appear to "vanish" for subsequent commands.
    _ensure_alembic_version_table(connection.execution_options(isolation_level="AUTOCOMMIT"))
    context.configure(connection=connection, target_metadata=target_metadata, include_object=_include_object)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
