"""Transferências entre contas, aplicações/resgates e day trade não podem inflar receita e despesa."""

from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Transaction
from app.services import quant_engine
from app.services.insights_engine import InsightsEngine
from app.services.quant_engine import QuantEngine

D = Decimal
_n = 0


def tx(session: AsyncSession, account: str, day: int, amount: float, kind: str, category: str, desc: str, month: int = 10) -> None:
    global _n
    _n += 1
    session.add(
        Transaction(
            id=f"f{_n:05d}", account_id=account, date=datetime(2026, month, day, 12), amount=D(str(amount)), type=kind,
            category=category, description=desc, currency_code="BRL", status="POSTED",
        )
    )  # fmt: skip


@pytest.fixture
async def seeded(session: AsyncSession, bank_id: str) -> None:
    session.add(Account(id="acc-bank2", item_id="item-1", type="BANK", name="Outro banco"))
    await session.commit()
    tx(session, bank_id, 5, 5000, "CREDIT", "Salário", "Salario empresa")  # receita real
    tx(session, bank_id, 6, 1000, "DEBIT", "Transfer - PIX", "PIX ENVIADO - FULANO")  # sai de A ...
    tx(session, "acc-bank2", 7, 1000, "CREDIT", "Transfer - PIX", "PIX RECEBIDO")  # ... e entra em B (3 dias de janela)
    tx(session, bank_id, 8, 50, "DEBIT", "Groceries", "Mercado Central")  # compra de 50
    tx(session, "acc-bank2", 8, 50, "CREDIT", "Transfer - PIX", "PIX RECEBIDO JOAO")  # coincidência: NÃO pareia
    tx(session, bank_id, 9, 3812, "DEBIT", "Transfer - PIX", "PIX ENVIADO - DES TIAGO CARNEIRO VIZOTO")  # para si mesmo
    tx(session, bank_id, 10, 2000, "CREDIT", "Investments", "RESGATE CDB")  # resgate
    tx(session, bank_id, 11, 300, "CREDIT", "Proceeds interests and dividends", "AJUSTE DAY-TRADE PR. 10/10 WIN")
    tx(session, bank_id, 12, 500, "DEBIT", "Proceeds interests and dividends", "AJUSTE DAY-TRADE PR. 11/10 WIN")
    tx(session, bank_id, 13, 100, "CREDIT", "Proceeds interests and dividends", "LIQUIDO DAS OPERACOES")
    await session.commit()


async def test_internal_moves_do_not_count(session: AsyncSession, seeded: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class Cfg:
        own_name_list = ["Tiago Carneiro Vizoto"]

    monkeypatch.setattr(quant_engine, "get_settings", lambda: Cfg())
    flows = await QuantEngine(session)._load_flows(datetime(2026, 10, 1), datetime(2026, 11, 1))
    # receita: salário 5000 + R$ 50 do "amigo"; day trade líquido = 300 - 500 + 100 = -100 (despesa)
    assert flows["income"].sum() == 5050
    assert flows["expense"].sum() == 50 + 100
    dt = flows[flows["category"] == "Day trade"]
    assert len(dt) == 1 and dt["expense"].iat[0] == 100 and dt["description"].iat[0].startswith("Resultado líquido")
    assert (flows["reason"] != "").sum() == 4  # par PIX (2) + PIX para si + resgate; day trade já virou 1 linha


async def test_without_own_names_self_pix_still_counts(session: AsyncSession, seeded: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class Cfg:
        own_name_list: list[str] = []

    monkeypatch.setattr(quant_engine, "get_settings", lambda: Cfg())
    flows = await QuantEngine(session)._load_flows(datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert flows["expense"].sum() == 3812 + 50 + 100


async def test_pairing_needs_different_accounts_and_window(session: AsyncSession, bank_id: str) -> None:
    session.add(Account(id="acc-bank2", item_id="item-1", type="BANK", name="Outro banco"))
    await session.commit()
    tx(session, bank_id, 1, 700, "DEBIT", "Transfer - PIX", "PIX ENVIADO X")
    tx(session, bank_id, 1, 700, "CREDIT", "Transfer - PIX", "PIX RECEBIDO Y")  # mesma conta: não pareia
    tx(session, bank_id, 2, 400, "DEBIT", "Transfer - PIX", "PIX ENVIADO Z")
    tx(session, "acc-bank2", 20, 400, "CREDIT", "Transfer - PIX", "PIX RECEBIDO Z")  # 18 dias depois: fora da janela
    await session.commit()
    flows = await QuantEngine(session)._load_flows(datetime(2026, 10, 1), datetime(2026, 11, 1))
    assert (flows["reason"] != "").sum() == 0
    assert flows["income"].sum() == 1100 and flows["expense"].sum() == 1100


async def test_flow_audit_lists_what_was_left_out(session: AsyncSession, seeded: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class Cfg:
        own_name_list = ["Tiago Carneiro Vizoto"]

    import app.services.insights_engine as ie

    monkeypatch.setattr(quant_engine, "get_settings", lambda: Cfg())
    monkeypatch.setattr(ie, "get_settings", lambda: Cfg())
    audit = await InsightsEngine(session).flow_audit(2026)
    by = {g.reason: g for g in audit.groups}
    assert by["pareada"].count == 2 and by["pareada"].money_in == 1000 and by["pareada"].money_out == 1000
    assert by["propria"].money_out == 3812 and by["investimento"].money_in == 2000
    assert by["day_trade"].count == 3 and by["day_trade"].money_in == 400 and by["day_trade"].money_out == 500
    assert audit.day_trade_net == -100 and (audit.counted_income, audit.counted_expense) == (5050, 150)
    assert not audit.warnings


async def test_audit_endpoint_warns_when_no_own_name(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/quant/flow-audit?year=2026")).json()
    assert body["groups"] == [] and "OWN_NAMES" in body["warnings"][0]
