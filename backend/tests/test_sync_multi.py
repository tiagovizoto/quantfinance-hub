"""Vários itemIds (um por banco no MeuPluggy): leitura do .env, falha isolada e resposta agregada."""

import httpx
import pytest

from app.api.routers import sync as sync_router
from app.core.config import Settings, split_item_ids
from app.services.pluggy_service import (
    PluggyAPIError,
    SyncResult,
    SyncSummary,
    sync_items,
)


def test_split_item_ids_accepts_commas_spaces_and_newlines() -> None:
    assert split_item_ids("a, b;c\n d ,a") == ["a", "b", "c", "d"]
    assert split_item_ids("") == [] and split_item_ids(None) == []


def test_settings_reads_one_or_many(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLUGGY_ITEM_ID", "id-1, id-2")
    assert Settings(_env_file=None).pluggy_item_ids == ["id-1", "id-2"]  # type: ignore[call-arg]
    monkeypatch.delenv("PLUGGY_ITEM_ID")
    monkeypatch.setenv("PLUGGY_ITEM_IDS", "x y")
    assert Settings(_env_file=None).pluggy_item_ids == ["x", "y"]  # type: ignore[call-arg]
    monkeypatch.delenv("PLUGGY_ITEM_IDS")
    assert Settings(_env_file=None).pluggy_item_ids == []  # type: ignore[call-arg]


class FakeService:
    def __init__(self, failing: dict[str, Exception]) -> None:
        self.failing, self.calls = failing, []

    async def sync_item(self, item_id: str, *, full_history: bool = False) -> SyncResult:
        self.calls.append(item_id)
        if item_id in self.failing:
            raise self.failing[item_id]
        return SyncResult(item_id=item_id, accounts=2, transactions=10, warnings=["aviso"])


async def test_one_failing_item_does_not_stop_the_others() -> None:
    service = FakeService({"bad": PluggyAPIError(404, "item em UPDATING", "/items/bad"), "boom": RuntimeError("x")})
    summary = await sync_items(service, ["a", "bad", "boom", "b"])  # type: ignore[arg-type]
    assert service.calls == ["a", "bad", "boom", "b"]
    assert [r.item_id for r in summary.results] == ["a", "b"]
    assert set(summary.errors) == {"bad", "boom"}
    total = summary.total
    assert (total.accounts, total.transactions, total.item_id) == (4, 20, "a, b")
    assert any("Item bad" in w for w in total.warnings)


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch):
    def install(summary: SyncSummary) -> None:
        async def run(item_ids, *, full_history=False):
            return summary

        monkeypatch.setattr(sync_router, "run_sync", run)

        async def no_snapshot() -> None: ...

        monkeypatch.setattr(sync_router, "_record_snapshot", no_snapshot)

    return install


async def test_endpoint_aggregates_and_reports_partial_failure(client: httpx.AsyncClient, fake_run) -> None:
    fake_run(SyncSummary(results=[SyncResult("a", accounts=1, transactions=5), SyncResult("b", accounts=2, transactions=7)], errors={"c": "falhou"}))
    body = (await client.post("/api/v1/sync")).json()
    assert (body["accounts"], body["transactions"], body["items"], body["failed"]) == (3, 12, ["a", "b"], {"c": "falhou"})


async def test_endpoint_502_when_every_item_fails(client: httpx.AsyncClient, fake_run) -> None:
    fake_run(SyncSummary(errors={"a": "erro 1", "b": "erro 2"}))
    response = await client.post("/api/v1/sync")
    assert response.status_code == 502 and "erro 1" in response.json()["detail"]
