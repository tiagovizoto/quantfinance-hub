"""Análises extras (/quant/cash-forecast, pace, category-change, structure, concentration, portfolio,
installments, money-graph, net-worth) contra contas feitas à mão."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pandas as pd
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Investment, Loan, Transaction
from app.services.insights_engine import InsightsEngine, pareto

D = Decimal
AS_OF = date(2026, 10, 16)
_n = 0


def add_tx(session: AsyncSession, account: str, when: tuple[int, int, int], amount: float, kind: str, category: str, desc: str, **extra: int) -> None:
    global _n
    _n += 1
    session.add(
        Transaction(
            id=f"i{_n:06d}", account_id=account, date=datetime(*when, 12), amount=D(str(amount)), type=kind,
            category=category, description=desc, currency_code="BRL", status="POSTED", **extra,
        )
    )  # fmt: skip


async def set_bank(session: AsyncSession, balance: float) -> None:
    bank = await session.get(Account, "acc-bank")
    assert bank is not None
    bank.balance = D(str(balance))
    await session.commit()


async def set_card(session: AsyncSession, limit: float, available: float, due: date | None) -> None:
    card = await session.get(Account, "acc-card")
    assert card is not None
    card.credit_limit, card.credit_available_limit, card.credit_balance_due_date = D(str(limit)), D(str(available)), due
    await session.commit()


def test_pareto_hand_computed() -> None:
    result = pareto(pd.Series({"A": 50.0, "B": 30.0, "C": 15.0, "D": 5.0, "Z": -10.0}))
    assert [i.name for i in result.items] == ["A", "B", "C", "D"]
    assert [i.cumulative for i in result.items] == [0.5, 0.8, 0.95, 1.0]
    assert (result.n_for_50, result.n_for_80, result.count) == (1, 2, 4)
    assert pareto(pd.Series(dtype=float)).items == []


async def test_pace_projects_month_end(session: AsyncSession, bank_id: str) -> None:
    # ago: 200 no dia 5 | set: 100 no dia 10 + 300 no dia 20 | out: 50 (dia 3) + 150 (dia 12)
    for when, amount in [((2026, 8, 5), 200), ((2026, 9, 10), 100), ((2026, 9, 20), 300), ((2026, 10, 3), 50), ((2026, 10, 12), 150)]:
        add_tx(session, bank_id, when, amount, "DEBIT", "Mercado", "Mercado Central")
    await session.commit()
    pace = await InsightsEngine(session).pace(AS_OF)
    assert pace.spent_to_date == 200
    assert pace.previous_label == "set/2026" and pace.previous_to_date == 100 and pace.previous_total == 400
    assert pace.months_in_average == 2
    assert pace.average_to_date == 150 and pace.typical_end == 300  # média de ago (200) e set (400) no total; (200+100)/2 até o dia 16
    assert pace.projected_end == 400  # 200 * 300 / 150
    assert pace.current[15] == 200 and pace.current[16] is None


async def test_pace_without_history(session: AsyncSession) -> None:
    pace = await InsightsEngine(session).pace(AS_OF)
    assert pace.projected_end is None and pace.warnings


async def test_category_change_modes(session: AsyncSession, bank_id: str) -> None:
    for when, amount, cat in [
        ((2025, 10, 4), 120, "Mercado"), ((2026, 8, 5), 200, "Mercado"), ((2026, 9, 20), 300, "Mercado"),
        ((2026, 9, 10), 100, "Lazer"), ((2026, 10, 12), 200, "Mercado"),
    ]:
        add_tx(session, bank_id, when, amount, "DEBIT", cat, f"{cat} loja")
    await session.commit()
    engine = InsightsEngine(session)
    prev = await engine.category_change(2026, 10, "previous_month")
    rows = {r.category: r for r in prev.rows}
    assert (rows["Mercado"].current, rows["Mercado"].base, rows["Mercado"].delta, rows["Mercado"].delta_pct) == (200, 300, -100, -0.3333)
    assert (rows["Lazer"].current, rows["Lazer"].delta, rows["Lazer"].delta_pct) == (0, -100, -1.0)
    assert (prev.current_total, prev.base_total) == (200, 400)
    year = await engine.category_change(2026, 10, "previous_year")
    assert year.base_total == 120 and year.rows[0].category == "Mercado" and year.rows[0].delta == 80
    avg = await engine.category_change(2026, 10, "average_6m")
    assert {r.category: r.base for r in avg.rows}["Mercado"] == 250  # (200 + 300) / 2 meses com gasto
    assert avg.warnings  # só 2 meses


async def test_structure_splits_fixed_variable_one_off(session: AsyncSession, bank_id: str) -> None:
    variable = [100, 300, 50, 400, 150, 250]
    for m in range(1, 7):
        add_tx(session, bank_id, (2026, m, 10), 1000, "DEBIT", "Moradia", "Aluguel apto")
        add_tx(session, bank_id, (2026, m, 7), variable[m - 1], "DEBIT", "Mercado", f"Loja {'abcdef'[m - 1]}x{m * 7}")
    add_tx(session, bank_id, (2026, 3, 20), 3000, "DEBIT", "Compras", "Televisao grande")
    await session.commit()
    result = await InsightsEngine(session).structure(2026)
    march = result.months[2]
    assert (march.fixed, march.variable, march.one_off, march.total) == (1000, 50, 3000, 4050)
    assert (result.fixed_total, result.variable_total, result.one_off_total) == (6000, 1250, 3000)
    assert result.fixed_share == pytest.approx(6000 / 10250, abs=1e-4)
    assert result.one_off_threshold == 1250  # 5 x mediana (250)
    assert result.fixed_items == ["Aluguel Apto"]


async def test_concentration_and_graph(session: AsyncSession, bank_id: str, card_id: str) -> None:
    add_tx(session, bank_id, (2026, 10, 5), 5000, "CREDIT", "Salário", "Salario empresa")
    add_tx(session, bank_id, (2026, 10, 8), 200, "DEBIT", "Mercado", "Supermercado Bom Preco")
    add_tx(session, card_id, (2026, 10, 9), 100, "DEBIT", "Lazer", "Cinema Central")
    await session.commit()
    engine = InsightsEngine(session)
    conc = await engine.concentration(2026, 10)
    assert conc.expense_total == 300
    assert [(i.name, i.share) for i in conc.categories.items] == [("Mercado", 0.6667), ("Lazer", 0.3333)]
    assert conc.categories.n_for_80 == 2

    graph = await engine.money_graph(2026, 10)
    assert (graph.income_total, graph.expense_total) == (5000, 300)
    nodes = {n.id: n for n in graph.nodes}
    links = {(link.source, link.target): link.value for link in graph.links}
    assert links[("inc:Salario Empresa", "acc:Conta corrente")] == 5000
    assert links[("acc:Conta corrente", "cat:Mercado")] == 200 and links[("acc:Cartão", "cat:Lazer")] == 100
    assert nodes["cat:Mercado"].value == 200 and nodes["acc:Cartão"].value == 100
    assert all(link.source in nodes and link.target in nodes for link in graph.links)
    # conservação: tudo que entra em cada categoria é o valor da categoria
    for node in nodes.values():
        if node.kind == "category":
            assert sum(v for (_, t), v in links.items() if t == node.id) == node.value


async def test_cash_forecast_variable_and_card(session: AsyncSession, bank_id: str) -> None:
    await set_bank(session, 1000)
    await set_card(session, 1000, 700, date(2026, 10, 25))  # fatura aberta de 300
    for k in range(1, 91):
        add_tx(session, bank_id, tuple((AS_OF - timedelta(days=k)).timetuple()[:3]), 10, "DEBIT", "Mercado", "Padaria")  # type: ignore[arg-type]
    await session.commit()
    forecast = await InsightsEngine(session).cash_forecast(AS_OF, 60)
    assert forecast.starting_balance == 1000 and forecast.daily_variable_spend == 10
    assert [(e.date, e.amount, e.kind) for e in forecast.events] == [(date(2026, 10, 25), -300, "card")]
    assert forecast.ending_balance == 1000 - 600 - 300  # 60 dias x 10 + fatura
    assert forecast.points[0].base == 990 and forecast.points[-1].date == AS_OF + timedelta(days=60)
    assert forecast.min_balance == 100 and forecast.first_negative_base is None
    assert all(p.low == p.base == p.high for p in forecast.points)  # gasto constante: sem incerteza


async def test_cash_forecast_loan_installments_and_negative(session: AsyncSession) -> None:
    await set_bank(session, 150)
    session.add(
        Loan(
            id="loan-1", item_id="item-1", product_name="Pessoal", outstanding_balance=D("1200"), cet_monthly=D("0"),
            remaining_installments=12, first_installment_due_date=date(2026, 1, 20),
        )
    )
    await session.commit()
    forecast = await InsightsEngine(session).cash_forecast(AS_OF, 60)
    assert [(e.date, e.amount) for e in forecast.events] == [(date(2026, 10, 20), -100), (date(2026, 11, 20), -100)]
    assert forecast.ending_balance == -50 and forecast.first_negative_base == date(2026, 11, 20)


async def test_portfolio(session: AsyncSession) -> None:
    session.add_all(
        [
            Investment(id="inv-1", item_id="item-1", type="FIXED_INCOME", subtype="CDB", asset_class="RENDA_FIXA", name="CDB Banco X",
                       net_balance=D("6000"), gross_balance=D("6100"), amount_invested=D("5000"), profit=D("1000"),
                       last_twelve_months_rate=D("14.65"), due_date=date(2027, 3, 15)),
            Investment(id="inv-2", item_id="item-1", type="EQUITY", subtype="STOCK", asset_class="RENDA_VARIAVEL", name="ABCD3",
                       net_balance=D("4000"), gross_balance=D("4000")),
        ]
    )  # fmt: skip
    await session.commit()
    from app.schemas.quant import MarketAssumptions

    result = await InsightsEngine(session).portfolio(AS_OF, MarketAssumptions())
    assert result.total == 10000 and result.matures_12m == 6000
    assert [(c.asset_class, c.share) for c in result.by_class] == [("RENDA_FIXA", 0.6), ("RENDA_VARIAVEL", 0.4)]
    cdb = result.positions[0]
    assert (cdb.rate_pct, cdb.rate_source, cdb.spread_vs_cdi, cdb.profit_pct) == (14.65, "realizada_12m", 1.0, 20.0)
    assert result.maturities[0].month == "2027-03" and result.maturities[0].items == ["CDB Banco X"]
    assert result.positions[1].rate_source in ("contratada", "estimada")


async def test_installments(session: AsyncSession, card_id: str) -> None:
    add_tx(session, card_id, (2026, 8, 20), 300, "DEBIT", "Compras", "Notebook Dell", installment_number=2, total_installments=10)
    add_tx(session, card_id, (2026, 9, 20), 300, "DEBIT", "Compras", "Notebook Dell", installment_number=3, total_installments=10)
    add_tx(session, card_id, (2026, 9, 21), 80, "DEBIT", "Compras", "Compra a vista", installment_number=1, total_installments=1)
    add_tx(session, card_id, (2026, 2, 1), 50, "DEBIT", "Compras", "Fone", installment_number=3, total_installments=3)  # já quitado
    await session.commit()
    result = await InsightsEngine(session).installments(AS_OF)
    assert len(result.purchases) == 1
    p = result.purchases[0]
    assert (p.number, p.total, p.remaining, p.remaining_total, p.ends_on) == (3, 10, 6, 1800, "2027-04")
    assert [m.month for m in result.months] == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03", "2027-04"]
    assert result.next_month == 300 and result.total_committed == 1800


async def test_installments_without_data_warns(session: AsyncSession) -> None:
    result = await InsightsEngine(session).installments(AS_OF)
    assert result.purchases == [] and result.warnings


async def test_net_worth_snapshot_upserts(session: AsyncSession) -> None:
    await set_bank(session, 1000)
    await set_card(session, 1000, 700, None)
    engine = InsightsEngine(session)
    point = await engine.record_snapshot(AS_OF)
    assert point.net_worth == 700 and point.card_debt == 300
    await set_bank(session, 1500)
    await engine.record_snapshot(AS_OF)  # mesmo dia: atualiza
    await engine.record_snapshot(AS_OF + timedelta(days=1))
    result = await engine.net_worth(AS_OF + timedelta(days=1))
    assert [h.day for h in result.history] == [AS_OF, AS_OF + timedelta(days=1)]
    assert result.history[0].net_worth == 1200 and result.current.net_worth == 1200 and not result.warnings


@pytest.mark.parametrize(
    "path",
    ["cash-forecast", "pace", "category-change?mode=average_6m", "structure?year=2026", "concentration?year=2026&month=10",
     "portfolio", "installments", "money-graph?year=2026", "net-worth"],
)
async def test_endpoints_respond_on_empty_database(client: httpx.AsyncClient, path: str) -> None:
    response = await client.get(f"/api/v1/quant/{path}")
    assert response.status_code == 200, response.text


async def test_endpoint_validation(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/quant/category-change?mode=foo")).status_code == 422
    assert (await client.get("/api/v1/quant/cash-forecast?days=3")).status_code == 422
