"""Fixtures: banco SQLite temporário + contas de teste (uma corrente, um cartão)."""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# A DATABASE_URL precisa existir ANTES de importar app.database (o engine é criado no import).
_TMP = Path(tempfile.mkdtemp(prefix="qfh-tests-"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP / 'test.db'}"

import httpx  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Account, Base, Item  # noqa: E402

BANK_ID = "acc-bank"
CARD_ID = "acc-card"


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        db.add(Item(id="item-1"))
        db.add_all(
            [
                Account(id=BANK_ID, item_id="item-1", type="BANK", name="Conta corrente", number="12345-6"),
                Account(id=CARD_ID, item_id="item-1", type="CREDIT", name="Cartão"),
            ]
        )
        await db.commit()
    async with AsyncSessionLocal() as db:
        yield db


@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def bank_id() -> str:
    return BANK_ID


@pytest.fixture
def card_id() -> str:
    return CARD_ID
