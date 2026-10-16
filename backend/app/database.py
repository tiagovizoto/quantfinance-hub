"""Engine e sessões assíncronas (SQLAlchemy 2.0 + aiosqlite).

O código é agnóstico de banco: para migrar para PostgreSQL basta trocar a
``DATABASE_URL`` (ex.: ``postgresql+asyncpg://...``). Os PRAGMAs abaixo só
são aplicados quando o backend é SQLite.
"""

import warnings
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def build_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Cria o AsyncEngine; em SQLite liga foreign keys, WAL e busy_timeout."""
    is_sqlite = make_url(database_url).get_backend_name() == "sqlite"

    connect_args: dict[str, Any] = {}
    if is_sqlite:
        connect_args["timeout"] = 30  # segundos esperando lock de escrita

    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if is_sqlite:
        # SQLite guarda NUMERIC como REAL. Com Numeric(15, 2) o erro de ponto
        # flutuante fica abaixo de 0,01 até ~9e13; o aviso é ruído nesse cenário.
        warnings.filterwarnings(
            "ignore",
            message=r".*does \*not\* support Decimal objects natively.*",
            category=SAWarning,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _configure_sqlite(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")  # FKs são OFF por padrão no SQLite
                cursor.execute("PRAGMA journal_mode=WAL")  # leitores não bloqueiam o escritor
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA busy_timeout=30000")
            finally:
                cursor.close()

    return engine


_settings = get_settings()

engine: AsyncEngine = build_engine(_settings.database_url, echo=_settings.sql_echo)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependência FastAPI: uma sessão por requisição. Commit é explícito."""
    async with AsyncSessionLocal() as session:
        yield session


async def dispose_engine() -> None:
    await engine.dispose()
