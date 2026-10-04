from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base
from typing import AsyncGenerator

from app.core.config import settings


# ============================================
# ENGINE — connection pool (created once at import time)
# ============================================
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG, 
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)


# ============================================
# SESSION FACTORY — produces sessions per request
# ============================================
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ============================================
# BASE — all models inherit from this
# ============================================
Base = declarative_base()


# ============================================
# FASTAPI DEPENDENCY — inject session into endpoints
# ============================================
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Yield a database session for the duration of a request.
    
    - Commits on success
    - Rolls back on exception
    - Always closes the session (even on error)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ============================================
# LIFECYCLE HELPERS — called from main.py
# ============================================
async def init_db() -> None:
    """Verify the database is reachable at startup."""
    async with engine.begin() as conn:
        await conn.run_sync(lambda _: None)


async def close_db() -> None:
    """Dispose of the connection pool at shutdown."""
    await engine.dispose()