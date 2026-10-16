"""/v2/transactions: paginação por cursor (``next`` -> ``after``) e fallback do nome do filtro de data."""

from datetime import date

import pytest

from app.core.config import Settings
from app.services.pluggy_service import (
    PluggyAPIError,
    PluggyClient,
    PluggySyncService,
    _cursor_from_next,
)


def test_cursor_from_next_variants() -> None:
    assert _cursor_from_next("https://api.pluggy.ai/v2/transactions?accountId=a&after=abc123") == "abc123"
    assert _cursor_from_next("/v2/transactions?after=x%3D") == "x="
    assert _cursor_from_next("rawcursor") == "rawcursor"
    assert _cursor_from_next(None) is None and _cursor_from_next("") is None
    assert _cursor_from_next("https://api.pluggy.ai/v2/transactions?accountId=a") is None


class Scripted(PluggyClient):
    """Cliente cujo ``request`` responde de um roteiro (sem rede)."""

    def __init__(self, responder) -> None:
        super().__init__(Settings(_env_file=None))  # type: ignore[call-arg]
        self.responder, self.calls = responder, []

    async def request(self, method, path, *, params=None):
        self.calls.append((path, dict(params or {})))
        return self.responder(path, dict(params or {}))


async def test_cursor_pagination_follows_next_until_null() -> None:
    pages = {
        None: {"results": [{"id": 1}, {"id": 2}], "next": "https://api.pluggy.ai/v2/transactions?after=c1"},
        "c1": {"results": [{"id": 3}], "next": "https://api.pluggy.ai/v2/transactions?after=c2"},
        "c2": {"results": [{"id": 4}], "next": None},
    }
    client = Scripted(lambda path, params: pages[params.get("after")])
    got = [r["id"] async for page in client.iter_cursor_pages("/v2/transactions", {"accountId": "a"}) for r in page]
    assert got == [1, 2, 3, 4]
    assert [c[1].get("after") for c in client.calls] == [None, "c1", "c2"]
    await client.aclose()


async def test_cursor_pagination_stops_on_repeated_cursor() -> None:
    client = Scripted(lambda path, params: {"results": [{"id": 1}], "next": "/v2/transactions?after=same"})
    pages = [p async for p in client.iter_cursor_pages("/v2/transactions")]
    assert len(pages) == 2  # a 2ª resposta repete o cursor: não entra em laço infinito
    await client.aclose()


async def test_date_filter_falls_back_to_legacy_name_on_400() -> None:
    def responder(path: str, params: dict) -> dict:
        if "dateFrom" in params:
            raise PluggyAPIError(400, "invalid param dateFrom", path)
        assert params["from"] == "2026-09-01"
        return {"results": [{"id": 9}], "next": None}

    client = Scripted(responder)
    service = PluggySyncService(client, None, Settings(_env_file=None))  # type: ignore[arg-type, call-arg]
    pages = [p async for p in service._transaction_pages("acc", date(2026, 9, 1))]
    assert pages == [[{"id": 9}]]
    assert [sorted(c[1]) for c in client.calls] == [["accountId", "dateFrom"], ["accountId", "from"]]
    await client.aclose()


async def test_other_errors_are_not_swallowed() -> None:
    def responder(path: str, params: dict) -> dict:
        raise PluggyAPIError(500, "boom", path)

    client = Scripted(responder)
    service = PluggySyncService(client, None, Settings(_env_file=None))  # type: ignore[arg-type, call-arg]
    with pytest.raises(PluggyAPIError):
        _ = [p async for p in service._transaction_pages("acc", date(2026, 9, 1))]
    await client.aclose()
