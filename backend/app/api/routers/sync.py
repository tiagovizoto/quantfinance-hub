import logging
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal, get_session
from app.models import Item
from app.schemas.sync import SyncResponse
from app.services.insights_engine import InsightsEngine
from app.services.pluggy_service import PluggyAPIError, PluggyAuthError, run_sync

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])


class ItemStatus(BaseModel):
    id: str
    connector_name: str | None
    status: str | None
    last_synced_at: datetime | None


class SyncStatus(BaseModel):
    configured_item_id: str | None  # compatibilidade: o primeiro
    configured_item_ids: list[str] = []
    items: list[ItemStatus]


async def _record_snapshot() -> None:
    """Grava a foto diária do patrimônio; falha aqui nunca derruba a sincronização."""
    try:
        async with AsyncSessionLocal() as session:
            await InsightsEngine(session).record_snapshot(date.today())
    except Exception:
        logger.exception("Falha ao gravar o snapshot de patrimônio")


async def _run(item_ids: list[str] | None, full_history: bool) -> SyncResponse:
    try:
        summary = await run_sync(item_ids, full_history=full_history)
    except ValueError as exc:  # itemId ausente
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (PluggyAuthError, PluggyAPIError) as exc:  # falha ao abrir a sessão com a Pluggy
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not summary.results:  # todos falharam
        raise HTTPException(status_code=502, detail="; ".join(f"{i}: {m}" for i, m in summary.errors.items()))
    await _record_snapshot()
    return SyncResponse(
        **summary.total.as_dict(), items=[r.item_id for r in summary.results], failed=summary.errors
    )


@router.get("/status", response_model=SyncStatus)
async def sync_status(session: Annotated[AsyncSession, Depends(get_session)]) -> SyncStatus:
    """Itens conectados e quando foram sincronizados pela última vez."""
    from app.core.config import get_settings

    rows = (await session.scalars(select(Item).order_by(Item.last_synced_at.desc()))).all()
    return SyncStatus(
        configured_item_id=(get_settings().pluggy_item_ids or [None])[0],
        configured_item_ids=get_settings().pluggy_item_ids,
        items=[
            ItemStatus(
                id=i.id, connector_name=i.connector_name, status=i.status, last_synced_at=i.last_synced_at
            )
            for i in rows
        ],
    )


@router.post("", response_model=SyncResponse)
async def sync_configured_item(full_history: bool = False) -> SyncResponse:
    """Sincroniza todos os itens de PLUGGY_ITEM_ID (um por banco conectado)."""
    return await _run(None, full_history)


@router.post("/{item_id}", response_model=SyncResponse)
async def sync_item(item_id: str, full_history: bool = False) -> SyncResponse:
    """Sincroniza contas, transações, faturas, investimentos e empréstimos do item."""
    return await _run([item_id], full_history)
