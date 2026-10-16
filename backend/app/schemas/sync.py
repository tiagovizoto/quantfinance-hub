from datetime import datetime

from pydantic import BaseModel


class SyncResponse(BaseModel):
    item_id: str  # vários itens: ids separados por vírgula
    accounts: int
    transactions: int
    bills: int
    investments: int
    loans: int
    warnings: list[str]
    started_at: datetime
    finished_at: datetime | None
    items: list[str] = []  # itens sincronizados com sucesso
    failed: dict[str, str] = {}  # itemId -> motivo
