"""
Create all database tables from SQLAlchemy models.

Usage:
    python create_tables.py           # Create tables (idempotent)
    python create_tables.py --drop    # Drop all tables first, then create
    python create_tables.py --check   # Only verify tables exist

Safety:
    - Idempotent: running it twice is safe.
    - create_all() only creates MISSING tables. It does not alter
      or drop existing ones.
    - For schema changes (columns, types, constraints), use Alembic.
"""

import asyncio
import sys
from argparse import ArgumentParser
from pathlib import Path

# Ensure project root is importable regardless of where the script
# is invoked from. Without this, `python create_tables.py` from a
# different CWD would fail to import `app`.
sys.path.insert(0, str(Path(__file__).parent.resolve()))

# Importing app.models.db is REQUIRED — it registers all models
# with Base.metadata. Without it, create_all() would silently
# create zero tables.
from app.models.db import WebhookReceipt, DeadLetterQueue  # noqa: F401

from app.core.database import Base, engine, close_db
from app.core.logging import setup_logging, get_logger
from sqlalchemy import text


logger = get_logger(__name__)


EXPECTED_TABLES = {"webhook_receipts", "dead_letter_queue"}


async def list_tables() -> set[str]:
    """Query PostgreSQL for all tables in the public schema."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_type = 'BASE TABLE'"
            )
        )
        return {row[0] for row in result.fetchall()}


async def create_tables(drop: bool = False) -> None:
    """Create tables; optionally drop them first."""

    logger.info("Starting table creation...")

    if drop:
        logger.warning("Dropping all existing tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        logger.info("Drop complete.")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Verify what actually exists
    tables = await list_tables()
    missing = EXPECTED_TABLES - tables

    if missing:
        logger.error(f"Missing tables after create: {missing}")
        raise RuntimeError(f"Table creation failed; missing: {missing}")

    logger.info(f"Tables present: {sorted(tables)}")


async def check_tables() -> None:
    """Verify expected tables exist without modifying anything."""
    tables = await list_tables()
    missing = EXPECTED_TABLES - tables

    if missing:
        logger.error(f"Missing tables: {missing}")
        sys.exit(1)

    logger.info(f"All expected tables present: {sorted(EXPECTED_TABLES)}")


async def main() -> None:
    parser = ArgumentParser(description="Manage database tables.")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop all tables before creating them.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only check whether tables exist; do not create.",
    )
    args = parser.parse_args()

    setup_logging("INFO")

    try:
        if args.check:
            await check_tables()
        else:
            await create_tables(drop=args.drop)
    finally:
        # Always release the connection pool — otherwise Python
        # exits with "Task was destroyed" warnings on asyncio.
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())