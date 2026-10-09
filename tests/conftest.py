from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.core.database import Base
from app.main import create_app
from app.repositories.unit_of_work import UnitOfWork
from app.services.idempotency_service import IdempotencyService
from app.services.pipeline_publisher import PipelinePublisher
import os
from dotenv import load_dotenv

load_dotenv()

# Read database credentials from environment variables
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "webhook_db")

if not DB_PASSWORD:
    raise ValueError("DB_PASSWORD environment variable is not set!")

DATABASE_URL = f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"


@pytest_asyncio.fixture(scope="function")
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Provides an isolated PostgreSQL AsyncSession for database repository testing."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
def mock_uow():
    """Provides a mocked UnitOfWork for API testing."""
    uow = MagicMock(spec=UnitOfWork)
    uow.receipts = MagicMock()
    uow.receipts.create = AsyncMock()
    uow.receipts.get_by_idempotency_key = AsyncMock()
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    return uow


@pytest.fixture
def mock_idempotency():
    """Provides a mocked IdempotencyService."""
    idem = MagicMock(spec=IdempotencyService)
    idem.check = AsyncMock(return_value=None)
    idem.store = AsyncMock()
    idem.update_response = AsyncMock()
    return idem


@pytest.fixture
def mock_pipeline():
    """Provides a mocked PipelinePublisher."""
    pipe = MagicMock(spec=PipelinePublisher)
    pipe.publish = AsyncMock()
    return pipe


@pytest_asyncio.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Provides an AsyncClient connected to the FastAPI application instance."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client