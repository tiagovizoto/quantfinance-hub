"""Explorador de gastos (/quant/spending) e curva juros x principal, contra contas feitas à mão."""

import random
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction
from app.services.quant_engine import DebtSpec, detect_recurring, merchant_key, simulate_payoff

D = Decimal


def tx(i: int, account: str, when: tuple[int, int, int], amount: float, kind: str, category: str, desc: str) -> Transaction:
    return Transaction(
        id=f"s{i:05d}", account_id=account, date=datetime(*when, 12), amount=D(str(amount)), type=kind,
        category=category, description=desc, currency_code="BRL", status="POSTED",
    )  # fmt: skip


@pytest.fixture
async def seeded(session: AsyncSession, bank_id: str, card_id: str) -> dict[str, float]:
    """2026, jan..jun: salário, aluguel, Netflix (recorrente), mercado variável, uma TV e movimentos internos."""
    rng = random.Random(7)
    rows: list[Transaction] = []
    expected: dict[str, float] = defaultdict(float)
    by_month: dict[int, float] = defaultdict(float)
    i = 0

    def add(account, when, amount, kind, category, desc, *, counts=True):
        nonlocal i
        i += 1
        rows.append(tx(i, account, when, amount, kind, category, desc))
        if counts and kind == "DEBIT":
            expected[category] += amount
            by_month[when[1]] += amount

    for m in range(1, 7):
        add(bank_id, (2026, m, 5), 5000, "CREDIT", "Salário", "Salario empresa", counts=False)
        add(bank_id, (2026, m, 10), 1500, "DEBIT", "Moradia", f"Aluguel apto {m}")
        add(card_id, (2026, m, 15), 39.90, "DEBIT", "Assinaturas", "NETFLIX.COM 123456")
        for _ in range(4):
            add(bank_id, (2026, m, rng.randint(1, 28)), round(rng.uniform(80, 220), 2), "DEBIT", "Mercado", "Compra no débito - Supermercado Bom Preço")
        add(bank_id, (2026, m, 20), 800, "DEBIT", "Cartão", "Pagamento de fatura", counts=False)  # interno
    add(card_id, (2026, 3, 8), 3000, "DEBIT", "Compras", "Loja de Eletronicos TV 55")
    add(card_id, (2026, 4, 2), 100, "CREDIT", "Compras", "Estorno compra", counts=False)  # estorno reduz despesa
    expected["Compras"] -= 100
    by_month[4] -= 100

    session.add_all(rows)
    await session.commit()
    return {**expected, "__total__": sum(expected.values()), **{f"m{k}": v for k, v in by_month.items()}}


def test_merchant_key_groups_variants() -> None:
    assert merchant_key("Compra no débito - Padaria Pão 123") == merchant_key("COMPRA NO DEBITO PADARIA PAO 456")
    assert merchant_key("NETFLIX.COM 123456") == "netflix com"
    assert merchant_key("PIX ENVIADO: João Silva") == "joao silva"
    assert merchant_key("") == "sem descricao"
    assert merchant_key("Pix") == "pix"  # só prefixos genéricos: mantém o que há


async def test_year_view_matches_hand_calculation(client: httpx.AsyncClient, seeded: dict[str, float]) -> None:
    response = await client.get("/api/v1/quant/spending", params={"year": 2026})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["expense_total"] == pytest.approx(seeded["__total__"], abs=0.01)
    assert body["income_total"] == pytest.approx(6 * 5000)
    assert body["savings_total"] == pytest.approx(30000 - seeded["__total__"], abs=0.01)

    cats = {c["category"]: c for c in body["categories"]}
    for name in ("Moradia", "Assinaturas", "Mercado", "Compras"):
        assert cats[name]["total"] == pytest.approx(seeded[name], abs=0.01), name
    assert "Cartão" not in cats and "Salário" not in cats, "fatura paga e salário não são gasto"
    assert [c["category"] for c in body["categories"]] == sorted(cats, key=lambda k: -cats[k]["total"])
    assert sum(c["share"] for c in body["categories"]) == pytest.approx(1.0, abs=0.01)

    mercado = cats["Mercado"]
    assert mercado["count"] == 24 and mercado["average"] == pytest.approx(seeded["Mercado"] / 24, abs=0.01)
    assert mercado["merchants"][0]["name"] == "Supermercado Bom Preço".lower().replace("ç", "c").title()
    assert sum(cats["Compras"]["monthly"]) == pytest.approx(2900.0)
    assert cats["Compras"]["monthly"][2] == pytest.approx(3000.0) and cats["Compras"]["monthly"][3] == pytest.approx(-100.0)

    assert body["top_transactions"][0]["amount"] == 3000.0 and body["top_transactions"][0]["category"] == "Compras"
    listed = {t["description"].split()[0] for t in body["top_transactions"]}
    assert "Aluguel" not in listed and "NETFLIX.COM" not in listed, "gastos fixos ficam fora dos maiores avulsos"
    assert any(t["description"].startswith("Compra no débito") for t in body["top_transactions"])
    assert sum(d["expense"] for d in body["days"]) == pytest.approx(seeded["__total__"], abs=0.01)
    assert sum(w["total"] for w in body["weekdays"]) == pytest.approx(seeded["__total__"], abs=0.01)
    assert body["period_start"] == "2026-01-01" and body["period_end"] == "2026-12-31"


async def test_month_view_filters_period_but_keeps_year_trend(client: httpx.AsyncClient, seeded: dict[str, float]) -> None:
    body = (await client.get("/api/v1/quant/spending", params={"year": 2026, "month": 4})).json()
    assert body["expense_total"] == pytest.approx(seeded["m4"], abs=0.01)
    assert body["period_start"] == "2026-04-01" and body["period_end"] == "2026-04-30"
    assert all(d["date"].startswith("2026-04") for d in body["days"])
    cats = {c["category"]: c for c in body["categories"]}
    assert "Compras" not in cats, "categoria com saldo líquido negativo (só estorno) não entra no mapa de gastos"
    assert body["expense_total"] < sum(c["total"] for c in body["categories"]), "mas o estorno abate o total do mês"
    assert len(cats["Mercado"]["monthly"]) == 12 and sum(cats["Mercado"]["monthly"]) > cats["Mercado"]["total"]
    leap = (await client.get("/api/v1/quant/spending", params={"year": 2026, "month": 2})).json()
    assert leap["period_end"] == "2026-02-28"


async def test_recurring_detection(client: httpx.AsyncClient, seeded: dict[str, float]) -> None:
    body = (await client.get("/api/v1/quant/spending", params={"year": 2026})).json()
    names = {r["name"]: r for r in body["recurring"]}
    assert names["Netflix Com"]["monthly_amount"] == pytest.approx(39.90)
    assert names["Netflix Com"]["yearly_cost"] == pytest.approx(39.90 * 12) and names["Netflix Com"]["months_seen"] == 6
    assert names["Aluguel Apto"]["monthly_amount"] == pytest.approx(1500.0)
    assert not any("Supermercado" in n for n in names), "4 compras/mês de valor variável não é recorrente"
    assert "Loja De Eletronicos Tv" not in names, "compra única não é recorrente"
    assert body["recurring_monthly_total"] == pytest.approx(sum(r["monthly_amount"] for r in body["recurring"]))


async def test_empty_periods(client: httpx.AsyncClient, seeded: dict[str, float]) -> None:
    other_year = (await client.get("/api/v1/quant/spending", params={"year": 2024})).json()
    assert other_year["expense_total"] == 0 and other_year["categories"] == [] and other_year["warnings"]
    empty_month = (await client.get("/api/v1/quant/spending", params={"year": 2026, "month": 11})).json()
    assert empty_month["expense_total"] == 0 and empty_month["categories"] == []
    assert (await client.get("/api/v1/quant/spending", params={"month": 13})).status_code == 422


def test_detect_recurring_rejects_irregular() -> None:
    import pandas as pd

    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-05", "2026-02-05", "2026-05-05"]),  # buraco de 2 meses: 3/5 = 60%
            "expense": [50.0, 50.0, 50.0],
            "category": ["X"] * 3,
            "key": ["spotify"] * 3,
        }
    )
    assert [r.name for r in detect_recurring(frame)] == ["Spotify"]
    sparse = frame.assign(date=pd.to_datetime(["2026-01-05", "2026-04-05", "2026-10-05"]))
    assert detect_recurring(sparse) == []
    varying = frame.assign(date=pd.to_datetime(["2026-01-05", "2026-02-05", "2026-03-05"]), expense=[50.0, 120.0, 20.0])
    assert detect_recurring(varying) == []


def test_interest_split_matches_closed_form() -> None:
    debt = DebtSpec("a", "A", 10_000.0, 0.02, 12, "PRICE")
    sim = simulate_payoff([debt])
    assert sim.interest_by_month[0] == pytest.approx(200.0)  # 2% de 10.000
    assert sum(sim.interest_by_month) == pytest.approx(sim.total_interest)
    pmt = debt.price_payment
    assert sum(sim.paid[0]) - sim.interest_by_month[0] == pytest.approx(pmt - 200.0)
    assert sum(sum(row) for row in sim.paid) - sum(sim.interest_by_month) == pytest.approx(10_000.0, abs=0.01)


async def test_loan_strategy_cost_curve(client: httpx.AsyncClient, session: AsyncSession) -> None:
    from app.models import Item, Loan

    session.add(Item(id="item-2"))
    session.add_all(
        [
            Loan(id="l1", item_id="item-2", product_name="Caro", outstanding_balance=D("20000"), cet_annual=D("30"),
                 cet_monthly=D("2.2124"), amortization_scheduled="PRICE", remaining_installments=24, total_installments=24),
            Loan(id="l2", item_id="item-2", product_name="Barato", outstanding_balance=D("15000"), cet_annual=D("8"),
                 cet_monthly=D("0.6434"), amortization_scheduled="PRICE", remaining_installments=36, total_installments=36),
        ]
    )
    await session.commit()
    body = (await client.get("/api/v1/quant/loan-strategy", params={"extra": 1000})).json()
    curve, strategies = body["cost_curve"], {s["key"]: s for s in body["strategies"]}
    assert len(curve) == len(body["debt_curve"]) - 1
    for key in ("baseline", "avalanche", "snowball"):
        interest = sum(p[f"{key}_interest"] for p in curve)
        principal = sum(p[f"{key}_principal"] for p in curve)
        assert interest == pytest.approx(strategies[key]["total_interest"], abs=0.5 * len(curve) * 0.01 + 0.5), key
        assert principal == pytest.approx(35_000.0, abs=1.0), key
    assert curve[0]["baseline_interest"] == pytest.approx(20000 * 0.022124 + 15000 * 0.006434, abs=0.5)
    assert date.fromisoformat(curve[0]["on"] + "-01") > date(2020, 1, 1)
