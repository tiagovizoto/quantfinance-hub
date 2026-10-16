from datetime import datetime

from pydantic import BaseModel


class SyncResponse(BaseModel):
    item_id: str
    accounts: int
    transactions: int
    bills: int
    investments: int
    loans: int
    warnings: list[str]
    started_at: datetime
    finished_at: datetime | None
