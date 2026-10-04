import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.db import WebhookReceipt, WebhookStatus
from app.models.db.enums import ProviderType, ErrorCategory
from app.repositories.unit_of_work import UnitOfWork, get_uow

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def async_session():
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


@pytest.mark.asyncio
async def test_uow_repository_lazy_initialization(async_session):
    uow = UnitOfWork(async_session)

    # Initial state
    assert uow._receipts is None
    assert uow._dead_letter is None

    # Access repositories
    repo1 = uow.receipts
    repo2 = uow.dead_letter

    assert repo1 is not None
    assert repo2 is not None

    # Confirm idempotency of property getter
    assert uow.receipts is repo1
    assert uow.dead_letter is repo2


@pytest.mark.asyncio
async def test_uow_atomic_commit(async_session):
    uow = UnitOfWork(async_session)

    receipt = await uow.receipts.create(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_uow_commit",
        event_type="payment.succeeded",
        payload={"amount": 500},
        verified=True,
    )

    await uow.commit()

    # Query back using same session to confirm persistence
    fetched = await uow.receipts.get_by_id(receipt.id)
    assert fetched is not None
    assert fetched.idempotency_key == "key_uow_commit"


@pytest.mark.asyncio
async def test_uow_rollback_on_failure(async_session):
    uow = UnitOfWork(async_session)

    receipt = await uow.receipts.create(
        provider=ProviderType.STRIPE.value,
        idempotency_key="key_uow_rollback",
        event_type="payment.failed",
        payload={"amount": 500},
        verified=True,
    )

    await uow.rollback()

    # Query back to verify the receipt was not persisted
    fetched = await uow.receipts.get_by_id(receipt.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_uow_session_scope_success(async_session):
    # Mock AsyncSessionLocal to return our test session
    with patch("app.repositories.unit_of_work.AsyncSessionLocal", return_value=async_session):
        async with UnitOfWork.session_scope() as uow:
            receipt = await uow.receipts.create(
                provider=ProviderType.GENERIC.value,
                idempotency_key="key_scope_success",
                event_type="user.created",
                payload={},
                verified=True,
            )
            receipt_id = receipt.id

        # Outside scope context, verify changes were committed
        fetched = await uow.receipts.get_by_id(receipt_id)
        assert fetched is not None
        assert fetched.idempotency_key == "key_scope_success"


@pytest.mark.asyncio
async def test_uow_session_scope_rollback_on_exception(async_session):
    receipt_id = None

    with patch("app.repositories.unit_of_work.AsyncSessionLocal", return_value=async_session):
        with pytest.raises(RuntimeError, match="Simulated Processing Error"):
            async with UnitOfWork.session_scope() as uow:
                receipt = await uow.receipts.create(
                    provider=ProviderType.GENERIC.value,
                    idempotency_key="key_scope_fail",
                    event_type="user.created",
                    payload={},
                    verified=True,
                )
                receipt_id = receipt.id
                raise RuntimeError("Simulated Processing Error")

    # Verify transaction rolled back
    uow = UnitOfWork(async_session)
    fetched = await uow.receipts.get_by_id(receipt_id)
    assert fetched is None


@pytest.mark.asyncio
async def test_get_uow_fastapi_dependency():
    mock_session = AsyncMock(spec=AsyncSession)

    with patch("app.repositories.unit_of_work.AsyncSessionLocal", return_value=mock_session):
        gen = get_uow()
        uow = await anext(gen)

        assert isinstance(uow, UnitOfWork)
        assert uow.session == mock_session

        # Cleanup generator
        with pytest.raises(StopAsyncIteration):
            await anext(gen)

        mock_session.close.assert_called_once()