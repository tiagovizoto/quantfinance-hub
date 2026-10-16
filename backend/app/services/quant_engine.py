"""Motor quantitativo: fluxo de caixa, anomalias (Z-score), Monte Carlo, arbitragem e risco.

Organização:
1. Helpers e classificação de fluxos
2. Cálculos puros (sem banco): recebem DataFrames/números e devolvem números
3. ``QuantEngine``: carrega os dados via SQLAlchemy e monta as respostas (schemas Pydantic)

Convenções:
- Valores monetários viram ``float`` ao entrar no pandas/NumPy (ok para análise;
  o armazenamento continua em Numeric/Decimal).
- Taxas anuais em fração (0.1365 = 13,65%), exceto campos vindos da Pluggy (em %).
- "Despesa" exclui pagamento de fatura e transferências entre contas próprias,
  para não contar a mesma saída duas vezes (compra no cartão + pagamento da fatura).
"""

import math
import re
import unicodedata
from calendar import monthrange
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Account, Investment, Loan, Transaction
from app.schemas.quant import (
    AnomaliesResponse,
    ArbitrageResponse,
    ArbitrageRow,
    CardUtilization,
    CashflowResponse,
    CategoryAnomaly,
    CostPoint,
    CreditRiskResponse,
    DebtPoint,
    FanPoint,
    LoanArbitrage,
    LoanPlan,
    LoanStrategyResponse,
    MarketAssumptions,
    MonteCarloInputs,
    MonteCarloResponse,
    MonthlyFlow,
    PayoffStep,
    RecurringExpense,
    SpendCategory,
    SpendDay,
    SpendingResponse,
    SpendMerchant,
    SpendTransaction,
    SpendWeekday,
    StrategyResult,
    TimelinePoint,
    WealthHorizon,
    YearCategory,
    YearMonth,
    YearSummaryResponse,
)
from app.services import flow_rules

MONTH_NAMES_PT: Final = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)  # fmt: skip
UNCATEGORIZED: Final = "Sem categoria"
DAYS_PER_MONTH: Final = 30.4375
IR_EXEMPT_SUBTYPES: Final = frozenset(
    {"LCI", "LCA", "LCD", "CRI", "CRA", "POUPANCA", "SAVINGS"}
)
FLAT_TAX_RATE: Final = (
    0.15  # aproximação de longo prazo p/ renda variável, fundos e RF tributada (MC)
)
HISTORY_MONTHS_LOADED: Final = 14

REFUND_MARKERS: Final = ("estorno", "reembolso", "refund", "cancelamento", "chargeback")


class InsufficientDataError(ValueError):
    """Dados insuficientes para a métrica pedida."""


# ===========================================================================
# 1. Helpers
# ===========================================================================
def _norm(text: str) -> str:
    stripped = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return stripped.lower()


def _num(value: Any) -> float | None:
    """float finito ou None (NaN/inf viram None para o JSON)."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _r(value: Any, digits: int = 2) -> float | None:
    number = _num(value)
    return None if number is None else round(number, digits)


def _r0(value: Any, digits: int = 2) -> float:
    return _r(value, digits) or 0.0


def classify_flows(
    df: pd.DataFrame, own_names: Sequence[str] = (), *, net_day_trade: bool = True
) -> pd.DataFrame:
    """Acrescenta ``income``, ``expense`` (>= 0, exceto estornos que reduzem despesa) e ``reason``.

    Entrada: date, amount, type, category, description, account_type (BANK|CREDIT) e, opcionais,
    account_name e tx_id. ``reason`` explica por que uma linha NÃO conta como receita/despesa
    (ver ``flow_rules.REASON_LABELS``); vazio = conta normalmente.
    """
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["amount"] = out["amount"].astype(float).abs()
    text = (out["category"].fillna("") + " " + out["description"].fillna("")).map(_norm)

    refund = text.map(lambda t: any(m in t for m in REFUND_MARKERS))
    is_card = out["account_type"].eq("CREDIT")
    is_credit_dir = out["type"].str.upper().eq("CREDIT")

    reason = pd.Series("", index=out.index)
    for label, mask in (
        ("day_trade", flow_rules._contains(text, flow_rules.DAY_TRADE_MARKERS)),
        (
            "emprestimo",
            flow_rules._contains(text, flow_rules.LOAN_PROCEEDS_MARKERS)
            & is_credit_dir
            & ~is_card,
        ),
        ("investimento", flow_rules._contains(text, flow_rules.INVESTMENT_MARKERS)),
        (
            "propria",
            flow_rules._contains(text, flow_rules.OWN_TRANSFER_MARKERS)
            | flow_rules.own_name_mask(
                out["description"],
                [*own_names, *flow_rules.detect_own_names(out, _norm)],
                _norm,
            ),
        ),
        ("cartao", flow_rules._contains(text, flow_rules.CARD_MARKERS)),
    ):
        reason = reason.mask(mask & reason.eq(""), label)
    # o outro lado de uma transferência própria já pode ter sido marcado pelo banco ("Same person
    # transfer"); ele continua valendo como par da entrada/saída correspondente na outra conta.
    paired = flow_rules.pair_transfers(
        out,
        text,
        reason.isin(["day_trade", "cartao", "investimento", "emprestimo"]),
        is_credit_dir,
    )
    reason = reason.mask(paired & reason.eq(""), "pareada")

    internal = reason.isin(
        ["cartao", "propria", "pareada", "investimento", "emprestimo"]
    )
    day_trade = reason.eq("day_trade")
    skip = internal | day_trade

    out["income"] = np.where(~is_card & is_credit_dir & ~skip, out["amount"], 0.0)
    out["expense"] = np.where(~skip & ~is_credit_dir, out["amount"], 0.0)
    # Crédito no cartão só reduz despesa se for estorno; o resto é pagamento de fatura (ignorado).
    out["expense"] = out["expense"] - np.where(
        is_card & is_credit_dir & refund & ~skip, out["amount"], 0.0
    )
    out["category"] = out["category"].fillna("Sem categoria")
    out["reason"] = reason
    return flow_rules.net_day_trade(out, day_trade) if net_day_trade else out


# ===========================================================================
# 2. Cálculos puros
# ===========================================================================
def monthly_table(flows: pd.DataFrame) -> pd.DataFrame:
    """Receita, despesa e poupança por mês (meses sem movimento entram com zero)."""
    columns = ["income", "expense", "savings", "savings_rate"]
    if flows.empty:
        return pd.DataFrame(columns=columns, index=pd.PeriodIndex([], freq="M"))
    grouped = flows.groupby(flows["date"].dt.to_period("M"))[
        ["income", "expense"]
    ].sum()
    full_index = pd.period_range(grouped.index.min(), grouped.index.max(), freq="M")
    table = grouped.reindex(full_index, fill_value=0.0)
    table["savings"] = table["income"] - table["expense"]
    table["savings_rate"] = np.where(
        table["income"] > 0, table["savings"] / table["income"], np.nan
    )
    return table


def complete_months(table: pd.DataFrame, as_of: date, window: int) -> pd.DataFrame:
    """Últimos ``window`` meses fechados (o mês corrente, parcial, fica de fora)."""
    return table[table.index < pd.Period(as_of, "M")].tail(window)


def price_payment(balance: float, monthly_rate: float, n: int) -> float:
    """Parcela constante (Price). Taxa zero -> amortização linear."""
    if n <= 0:
        return 0.0
    if monthly_rate <= 0:
        return balance / n
    return balance * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -n)


def principal_schedule(
    balance: float, monthly_rate: float | None, n: int, horizon: int
) -> np.ndarray:
    """Principal amortizado em cada mês (tabela Price); sem taxa, amortização linear."""
    schedule = np.zeros(horizon)
    if n <= 0 or balance <= 0:
        return schedule
    rate = monthly_rate or 0.0
    payment = price_payment(balance, rate, n)
    remaining = balance
    for t in range(min(n, horizon)):
        principal = payment - remaining * rate
        schedule[t] = principal
        remaining -= principal
    return schedule


def ir_regressive_rate(days_held: float) -> float:
    """Tabela regressiva do IR sobre renda fixa."""
    if days_held <= 180:
        return 0.225
    if days_held <= 360:
        return 0.20
    if days_held <= 720:
        return 0.175
    return 0.15


@dataclass(frozen=True, slots=True)
class Position:
    id: str
    name: str | None
    asset_class: str
    subtype: str | None
    indexer: str | None
    rate: float | None  # % do indexador (ex.: 110) ou taxa pré em % a.a.
    fixed_annual_rate: float | None  # % a.a.
    annual_rate: float | None  # % a.a.
    last_twelve_months_rate: float | None  # % 12m
    gross: float
    net: float
    invested: float | None
    issue_date: date | None

    @property
    def tax_exempt(self) -> bool:
        return (self.subtype or "").upper() in IR_EXEMPT_SUBTYPES


def expected_gross_return(pos: Position, a: MarketAssumptions) -> tuple[float, bool]:
    """Retorno bruto anual esperado e se foi estimado a partir dos dados da posição."""
    if pos.asset_class == "RENDA_VARIAVEL":
        return a.equity_return_annual, False
    if pos.asset_class != "RENDA_FIXA":
        return a.funds_return_annual, False

    if pos.indexer in {"CDI", "SELIC"} and pos.rate:
        multiple = (
            pos.rate / 100 if pos.rate >= 5 else pos.rate
        )  # 110 ou 1.10 -> 110% do CDI
        return a.cdi_annual * multiple, True
    if pos.indexer == "PRE":
        pre = next(
            (v for v in (pos.fixed_annual_rate, pos.annual_rate, pos.rate) if v), None
        )
        if pre:
            return pre / 100, True
    if pos.indexer == "IPCA":
        spread = next(
            (v for v in (pos.fixed_annual_rate, pos.rate) if v is not None), 0.0
        )
        return (1 + a.ipca_annual) * (1 + spread / 100) - 1, True
    reported = next(
        (v for v in (pos.annual_rate, pos.last_twelve_months_rate) if v), None
    )
    if reported:
        return reported / 100, True
    return a.cdi_annual, False


def _class_vol(asset_class: str, a: MarketAssumptions) -> float:
    return {
        "RENDA_FIXA": a.fixed_income_vol_annual,
        "RENDA_VARIAVEL": a.equity_vol_annual,
    }.get(asset_class, a.funds_vol_annual)


def portfolio_return_and_vol(
    positions: Sequence[Position], a: MarketAssumptions
) -> tuple[float, float, int]:
    """Retorno anual líquido (aprox.) e volatilidade anual da carteira, ponderados por saldo.

    Variância com correlação uniforme ρ: Σ s_i² + ρ·((Σ s_i)² − Σ s_i²), s_i = w_i·σ_i.
    Retorna (retorno, vol, nº de posições com retorno assumido por falta de dado).
    """
    values = np.array([max(p.net, 0.0) for p in positions])
    total = values.sum()
    if total <= 0:
        return 0.0, 0.0, 0
    weights = values / total
    returns, vols, defaulted = [], [], 0
    for pos in positions:
        gross, from_data = expected_gross_return(pos, a)
        defaulted += 0 if from_data or pos.asset_class != "RENDA_FIXA" else 1
        returns.append(gross * (1 - (0.0 if pos.tax_exempt else FLAT_TAX_RATE)))
        vols.append(_class_vol(pos.asset_class, a))
    s = weights * np.array(vols)
    variance = float((s**2).sum() + a.asset_correlation * (s.sum() ** 2 - (s**2).sum()))
    return float(weights @ np.array(returns)), math.sqrt(max(variance, 0.0)), defaulted


def simulate_net_worth(
    *,
    initial_portfolio: float,
    other_net: float,
    savings_mu: float,
    savings_sigma: float,
    portfolio_return_annual: float,
    portfolio_vol_annual: float,
    debt_principal: np.ndarray,
    n_sims: int = 10_000,
    seed: int | None = None,
) -> np.ndarray:
    """Monte Carlo do patrimônio líquido. Retorna matriz (n_sims, meses).

    Dinâmica mensal:  P ← P·(1 + r) + s     (r ~ N(μ_m, σ_m),  s ~ N(μ_s, σ_s))
                      X ← X + principal_t    (X = caixa − dívidas; amortização determinística)
                      PL = P + X
    ``s`` é a poupança já líquida das parcelas (que saem como despesa); por isso só o principal
    amortizado soma ao patrimônio (os juros já estão em ``s``).
    """
    horizon = len(debt_principal)
    rng = np.random.default_rng(seed)
    mu_m = (1.0 + portfolio_return_annual) ** (1 / 12) - 1.0
    sigma_m = portfolio_vol_annual / math.sqrt(12)
    returns = np.clip(rng.normal(mu_m, sigma_m, size=(n_sims, horizon)), -0.95, None)
    savings = rng.normal(savings_mu, savings_sigma, size=(n_sims, horizon))

    portfolio = np.full(n_sims, initial_portfolio, dtype=float)
    other = float(other_net)
    paths = np.empty((n_sims, horizon))
    for t in range(horizon):
        portfolio = portfolio * (1.0 + returns[:, t]) + savings[:, t]
        other += float(debt_principal[t])
        paths[:, t] = portfolio + other
    return paths


def evaluate_debt_vs_investment(
    *,
    gross_now: float,
    invested: float,
    gross_annual_return: float,
    tax_exempt: bool,
    days_held_now: float,
    horizon_months: int,
    debt_annual_rate: float,
    discount_annual: float,
) -> dict[str, float]:
    """Compara, no horizonte T, "resgatar e amortizar" vs "manter investido" (valores por posição).

    - Amortizar: resgata hoje (paga IR regressivo sobre o ganho acumulado) e o líquido passa a
      "render" a taxa da dívida evitada:  A_T = líquido_hoje · (1 + CET)^T
    - Manter: o bruto cresce à taxa do papel e o IR é cobrado só no horizonte:
      B_T = bruto_T − IR(dias_T)·ganho_T
    - VPL da vantagem de manter = (B_T − A_T) / (1 + desconto)^T
    """
    years = horizon_months / 12.0
    tax_now = (
        0.0
        if tax_exempt
        else ir_regressive_rate(days_held_now) * max(gross_now - invested, 0.0)
    )
    net_now = gross_now - tax_now
    value_amortize = net_now * (1.0 + debt_annual_rate) ** years

    gross_horizon = gross_now * (1.0 + gross_annual_return) ** years
    days_horizon = days_held_now + horizon_months * DAYS_PER_MONTH
    tax_horizon = (
        0.0
        if tax_exempt
        else ir_regressive_rate(days_horizon) * max(gross_horizon - invested, 0.0)
    )
    value_keep = gross_horizon - tax_horizon

    net_annual = (
        (value_keep / net_now) ** (1.0 / years) - 1.0
        if net_now > 0 and value_keep > 0
        else float("nan")
    )
    return {
        "net_now": net_now,
        "value_amortize": value_amortize,
        "value_keep": value_keep,
        "vpl_advantage_keep": (value_keep - value_amortize)
        / (1.0 + discount_annual) ** years,
        "net_annual_return": net_annual,
    }


@dataclass(frozen=True, slots=True)
class DebtSpec:
    id: str
    name: str
    balance: float
    rate: float  # taxa mensal (fração), aproximada pelo CET
    n: int  # parcelas restantes
    amortization: str  # "PRICE" | "SAC"

    @property
    def price_payment(self) -> float:
        return price_payment(self.balance, self.rate, self.n)

    @property
    def first_installment(self) -> float:
        if self.amortization == "SAC":
            return self.balance / self.n + self.balance * self.rate
        return self.price_payment


@dataclass(slots=True)
class PayoffSim:
    months: int
    finished: bool
    total_interest: float
    interest_by_debt: dict[str, float]
    payoff_month: dict[str, int]
    remaining: list[float]  # saldo total no mês 0..months
    paid: list[list[float]]  # [mês-1][i-ésima dívida]
    interest_by_month: list[float]  # juros cobrados em cada mês 1..months


_EPS: Final = 0.005


def simulate_payoff(
    debts: Sequence[DebtSpec],
    *,
    extra: float = 0.0,
    mode: str = "baseline",
    priority: Sequence[str] | None = None,
    max_months: int = 600,
) -> PayoffSim:
    """Simula a quitação mês a mês.

    - ``baseline``: cada dívida paga só a própria parcela até acabar.
    - ``avalanche`` / ``snowball``: o orçamento mensal é constante (soma das parcelas iniciais
      + ``extra``); quando uma dívida acaba, a parcela dela rola para a próxima da fila.
      Avalanche prioriza a maior taxa; bola de neve, o menor saldo atual.
    - ``priority``: fila fixa de ids (usada em testes para provar que o avalanche é ótimo).
    """
    index = {d.id: k for k, d in enumerate(debts)}
    pmt = {d.id: d.price_payment for d in debts}
    balance = {d.id: d.balance for d in debts}
    interest_by = {d.id: 0.0 for d in debts}
    payoff: dict[str, int] = {}
    capacity = sum(d.first_installment for d in debts) + extra
    remaining = [sum(balance.values())]
    paid_rows: list[list[float]] = []
    interest_rows: list[float] = []

    month = 0
    while month < max_months and any(b > _EPS for b in balance.values()):
        month += 1
        row = [0.0] * len(debts)
        active = [d for d in debts if balance[d.id] > _EPS]
        spent = 0.0
        month_interest = 0.0
        for d in active:
            interest = balance[d.id] * d.rate
            month_interest += interest
            balance[d.id] += interest
            interest_by[d.id] += interest
            due = d.balance / d.n + interest if d.amortization == "SAC" else pmt[d.id]
            pay = min(due, balance[d.id])
            balance[d.id] -= pay
            row[index[d.id]] += pay
            spent += pay

        if mode != "baseline":
            budget = capacity - spent
            if priority is not None:
                ranked = sorted(
                    active,
                    key=lambda d: (
                        priority.index(d.id) if d.id in priority else len(priority)
                    ),
                )
            elif mode == "avalanche":
                ranked = sorted(active, key=lambda d: (-d.rate, balance[d.id]))
            else:
                ranked = sorted(active, key=lambda d: balance[d.id])
            for d in ranked:
                if budget <= _EPS:
                    break
                extra_pay = min(budget, balance[d.id])
                balance[d.id] -= extra_pay
                row[index[d.id]] += extra_pay
                budget -= extra_pay

        for d in active:
            if balance[d.id] <= _EPS:
                balance[d.id] = 0.0
                payoff.setdefault(d.id, month)
        remaining.append(sum(balance.values()))
        paid_rows.append(row)
        interest_rows.append(month_interest)

    return PayoffSim(
        months=month,
        finished=all(b <= _EPS for b in balance.values()),
        total_interest=sum(interest_by.values()),
        interest_by_debt=interest_by,
        payoff_month=payoff,
        remaining=remaining,
        paid=paid_rows,
        interest_by_month=interest_rows,
    )


def add_months_label(as_of: date, months: int) -> str:
    """AAAA-MM do mês `months` após o mês de `as_of` (mês 1 = próximo mês)."""
    total = as_of.year * 12 + (as_of.month - 1) + months
    return f"{total // 12}-{total % 12 + 1:02d}"


def category_zscores(
    flows: pd.DataFrame,
    as_of: date,
    *,
    window: int = 6,
    threshold: float = 2.0,
    min_days_for_projection: int = 7,
) -> tuple[list[CategoryAnomaly], list[str], list[str]]:
    """Z-score do gasto do mês corrente por categoria vs. média/desvio dos últimos ``window`` meses."""
    warnings: list[str] = []
    table = monthly_table(flows)
    base = complete_months(table, as_of, window)
    base_months = list(base.index)
    if len(base_months) < 3:
        warnings.append("Menos de 3 meses fechados de histórico: Z-score indisponível.")

    spend = flows[flows["expense"] != 0]
    if spend.empty:
        return (
            [],
            [str(m) for m in base_months],
            warnings + ["Sem despesas no período."],
        )
    by_month = spend.pivot_table(
        index=spend["date"].dt.to_period("M"),
        columns="category",
        values="expense",
        aggfunc="sum",
        fill_value=0.0,
    )
    current = pd.Period(as_of, "M")
    current_spend = (
        by_month.loc[current] if current in by_month.index else pd.Series(dtype=float)
    )
    baseline = by_month.reindex(
        base_months, fill_value=0.0
    )  # mês sem gasto na categoria = 0

    days_in_month = current.days_in_month
    projectable = as_of.day >= min_days_for_projection
    results: list[CategoryAnomaly] = []
    for category in sorted(set(baseline.columns) | set(current_spend.index)):
        series = (
            baseline[category]
            if category in baseline.columns
            else pd.Series(0.0, index=base_months)
        )
        mtd = float(current_spend.get(category, 0.0))
        projected = mtd * days_in_month / as_of.day if projectable else mtd
        mean = float(series.mean()) if len(series) else 0.0
        std = float(series.std(ddof=1)) if len(series) >= 2 else float("nan")

        z: float | None = None
        if len(base_months) >= 3 and std and math.isfinite(std) and std > 0:
            z = (projected - mean) / std
        results.append(
            CategoryAnomaly(
                category=category,
                current_month_to_date=_r0(mtd),
                projected_month=_r0(projected),
                baseline_mean=_r0(mean),
                baseline_std=_r(std),
                z_score=_r(z, 3),
                z_basis="projected" if projectable else "month_to_date",
                p_value_upper=_r(stats.norm.sf(z), 4) if z is not None else None,
                is_anomaly=z is not None and abs(z) >= threshold,
            )
        )
    results.sort(key=lambda c: (c.z_score is None, -abs(c.z_score or 0.0)))
    return results, [str(m) for m in base_months], warnings


# ===========================================================================
# 2b. Explorador de gastos: agrupamento por comerciante e recorrentes
# ===========================================================================
_MERCHANT_STOP: Final = frozenset(
    [
        "compra",
        "no",
        "debito",
        "credito",
        "pix",
        "enviado",
        "recebido",
        "transferencia",
        "enviada",
        "recebida",
        "pelo",
        "pagamento",
        "de",
        "em",
        "a",
        "pag",
        "pg",
        "mp",
        "ifd",
        "da",
        "do",
        "dos",
        "das",
        "para",
    ]
)
_WEEKDAYS_PT: Final = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")


def merchant_key(description: str | None) -> str:
    """Chave estável do comerciante: sem acento/números/prefixos genéricos, 3 primeiras palavras.

    "Compra no débito - Padaria Pão 123" e "COMPRA NO DEBITO PADARIA PAO 456" caem no mesmo grupo.
    """
    tokens = [
        t
        for t in re.sub(r"[^a-z0-9]+", " ", _norm(description or "")).split()
        if not t.isdigit()
    ]
    trimmed = list(tokens)
    while trimmed and trimmed[0] in _MERCHANT_STOP:
        trimmed.pop(0)
    return " ".join((trimmed or tokens)[:3]) or "sem descricao"


def recurring_groups(
    frame: pd.DataFrame, value: str = "expense"
) -> list[dict[str, Any]]:
    """Grupos que se repetem todo mês com valor parecido (assinaturas, mensalidades, aluguel, salário...).

    Exige: >= 3 meses distintos, presença em >= 60% dos meses do intervalo, no máximo ~2 lançamentos
    por mês ativo e >= 70% dos valores a ±15% da mediana. Espera colunas date, category, key e ``value``
    (``expense`` ou ``income``). Devolve também o dia típico do mês, usado na projeção de caixa.
    """
    found: list[dict[str, Any]] = []
    positive = frame[frame[value] > 0]
    for key, group in positive.groupby("key"):
        months = group["date"].dt.to_period("M")
        seen = int(months.nunique())
        if seen < 3:
            continue
        span = (months.max() - months.min()).n + 1
        per_month = group.groupby(months)[value].agg(["sum", "count"])
        median = float(group[value].median())
        close = float(((group[value] - median).abs() <= 0.15 * median).mean())
        if seen / span < 0.6 or float(per_month["count"].mean()) > 2.0 or close < 0.7:
            continue
        found.append(
            {
                "key": str(key),
                "category": str(group["category"].mode().iat[0]),
                "monthly": float(per_month["sum"].mean()),
                "median": median,
                "day": int(group["date"].dt.day.median()),
                "seen": seen,
                "last_seen": group["date"].max().date(),
            }
        )
    return found


def detect_recurring(spend: pd.DataFrame) -> list[RecurringExpense]:
    """Gastos fixos do período (ver ``recurring_groups``), do maior para o menor valor mensal."""
    found = [
        RecurringExpense(
            name=g["key"].title(),
            category=g["category"],
            monthly_amount=_r0(g["monthly"]),
            yearly_cost=_r0(g["monthly"] * 12),
            months_seen=g["seen"],
            last_seen=g["last_seen"],
        )
        for g in recurring_groups(spend, "expense")
    ]
    return sorted(found, key=lambda r: -r.monthly_amount)


# ===========================================================================
# 3. Engine (carrega dados via ORM e monta as respostas)
# ===========================================================================
class QuantEngine:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- carga de dados -----------------------------------------------------
    async def _flows(self, as_of: date) -> pd.DataFrame:
        since = (
            pd.Timestamp(as_of) - pd.DateOffset(months=HISTORY_MONTHS_LOADED)
        ).replace(day=1)
        return await self._load_flows(since.to_pydatetime())

    async def _load_flows(
        self,
        since: datetime,
        until: datetime | None = None,
        *,
        net_day_trade: bool = True,
    ) -> pd.DataFrame:
        """Transações em [since, until) já classificadas em receita/despesa."""
        statement = (
            select(
                Transaction.date,
                Transaction.amount,
                Transaction.type,
                Transaction.category,
                Transaction.description,
                Account.type.label("account_type"),
                Transaction.id.label("tx_id"),
                Account.name.label("account_name"),
            )
            .join(Account, Account.id == Transaction.account_id)
            .where(Transaction.date >= since)
        )
        # Parcelas futuras do cartão (ex.: 3/10 em 30/11) ainda não foram gastas.
        tomorrow = datetime.combine(
            date.today() + timedelta(days=1), datetime.min.time()
        )
        until = tomorrow if until is None else min(until, tomorrow)
        statement = statement.where(Transaction.date < until)
        rows = (await self._session.execute(statement)).all()
        columns = [
            "date",
            "amount",
            "type",
            "category",
            "description",
            "account_type",
            "tx_id",
            "account_name",
        ]
        raw = pd.DataFrame([tuple(r) for r in rows], columns=columns)
        if raw.empty:
            return pd.DataFrame(
                {
                    "date": pd.to_datetime([]),
                    "category": pd.Series(dtype=str),
                    "income": pd.Series(dtype=float),
                    "expense": pd.Series(dtype=float),
                    "reason": pd.Series(dtype=str),
                }
            )
        raw["amount"] = raw["amount"].map(float)
        return classify_flows(
            raw, get_settings().own_name_list, net_day_trade=net_day_trade
        )

    async def _liquidity(self, include_investments: bool) -> float:
        bank = await self._session.scalar(
            select(func.coalesce(func.sum(Account.balance), 0)).where(
                Account.type == "BANK", Account.is_active.is_(True)
            )
        )
        total = float(bank or 0)
        if include_investments:
            positions = await self._positions()
            total += sum(
                p.net for p in positions if p.asset_class in {"RENDA_FIXA", "FUNDOS"}
            )
        return total

    async def _positions(self) -> list[Position]:
        rows = (
            await self._session.scalars(
                select(Investment).where(Investment.is_active.is_(True))
            )
        ).all()
        out: list[Position] = []
        for inv in rows:
            gross = _num(inv.gross_balance)
            net = _num(inv.net_balance)
            if gross is None and net is None:
                continue
            out.append(
                Position(
                    id=inv.id,
                    name=inv.name,
                    asset_class=inv.asset_class,
                    subtype=inv.subtype,
                    indexer=inv.indexer,
                    rate=_num(inv.rate),
                    fixed_annual_rate=_num(inv.fixed_annual_rate),
                    annual_rate=_num(inv.annual_rate),
                    last_twelve_months_rate=_num(inv.last_twelve_months_rate),
                    gross=gross if gross is not None else net or 0.0,
                    net=net if net is not None else gross or 0.0,
                    invested=_num(inv.amount_invested),
                    issue_date=inv.issue_date,
                )
            )
        return out

    async def _loans(self) -> list[Loan]:
        return list(
            (
                await self._session.scalars(
                    select(Loan).where(
                        Loan.is_active.is_(True), Loan.outstanding_balance.is_not(None)
                    )
                )
            ).all()
        )

    async def _cards(self) -> list[Account]:
        return list(
            (
                await self._session.scalars(
                    select(Account).where(
                        Account.type == "CREDIT", Account.is_active.is_(True)
                    )
                )
            ).all()
        )

    @staticmethod
    def _card_used(card: Account) -> float:
        limit, available = _num(card.credit_limit), _num(card.credit_available_limit)
        if limit is not None and available is not None:
            return max(limit - available, 0.0)
        return abs(_num(card.balance) or 0.0)

    # -- 0. para onde foi o dinheiro (visão anual) ---------------------------
    async def year_summary(self, year: int, today: date) -> YearSummaryResponse:
        """Receitas, despesas e categorias x mês de um ano civil (mesmas regras do fluxo de caixa)."""
        year_rows = await self._session.execute(
            select(extract("year", Transaction.date)).distinct()
        )
        available = sorted(
            {int(r[0]) for r in year_rows.all() if r[0] is not None}, reverse=True
        )

        flows = await self._load_flows(datetime(year, 1, 1), datetime(year + 1, 1, 1))
        warnings: list[str] = []
        months_with_data = (
            sorted({int(m) for m in flows["date"].dt.month.unique()})
            if not flows.empty
            else []
        )
        if not months_with_data:
            warnings.append(f"Nenhuma transação importada em {year}.")

        flows = flows.assign(month=flows["date"].dt.month)
        by_month = (
            flows.groupby("month")[["income", "expense"]]
            .sum()
            .reindex(range(1, 13), fill_value=0.0)
            if not flows.empty
            else pd.DataFrame(0.0, index=range(1, 13), columns=["income", "expense"])
        )
        months = [
            YearMonth(
                month=m,
                income=_r0(by_month.loc[m, "income"]),
                expense=_r0(by_month.loc[m, "expense"]),
                savings=_r0(by_month.loc[m, "income"] - by_month.loc[m, "expense"]),
                has_data=m in months_with_data,
            )
            for m in range(1, 13)
        ]
        income_total = float(by_month["income"].sum())
        expense_total = float(by_month["expense"].sum())

        categories: list[YearCategory] = []
        spend = flows[flows["expense"] != 0] if not flows.empty else flows
        if not spend.empty:
            grid = (
                spend.groupby(["category", "month"])["expense"].sum().unstack(fill_value=0.0)
                .reindex(columns=range(1, 13), fill_value=0.0)
            )  # fmt: skip
            for name, row in grid.iterrows():
                total = float(row.sum())
                categories.append(
                    YearCategory(
                        category=str(name),
                        total=_r0(total),
                        share=_r(total / expense_total, 4)
                        if expense_total > 0
                        else None,
                        monthly=[_r0(v) for v in row.tolist()],
                    )
                )
            categories.sort(key=lambda c: -c.total)

        if months_with_data:
            if year == today.year:
                warnings.append(
                    f"Ano em andamento: {MONTH_NAMES_PT[today.month - 1]} está parcial."
                )
            if months_with_data[0] > 1:
                warnings.append(
                    f"O histórico importado de {year} começa em {MONTH_NAMES_PT[months_with_data[0] - 1]}; "
                    "os meses anteriores não têm transações."
                )
            uncategorized = next(
                (c.total for c in categories if c.category == UNCATEGORIZED), 0.0
            )
            if expense_total > 0 and uncategorized / expense_total >= 0.15:
                warnings.append(
                    f"{uncategorized / expense_total:.0%} das despesas estão sem categoria."
                )

        return YearSummaryResponse(
            year=year,
            available_years=available,
            is_partial=year >= today.year
            or (bool(months_with_data) and months_with_data[0] > 1),
            months_with_data=months_with_data,
            income_total=_r0(income_total),
            expense_total=_r0(expense_total),
            savings_total=_r0(income_total - expense_total),
            savings_rate=_r((income_total - expense_total) / income_total, 4)
            if income_total > 0
            else None,
            months=months,
            categories=categories,
            warnings=warnings,
        )

    # -- 1. fluxo de caixa --------------------------------------------------
    async def cashflow(
        self, as_of: date, *, window: int = 6, include_investments: bool = False
    ) -> CashflowResponse:
        flows = await self._flows(as_of)
        table = monthly_table(flows)
        base = complete_months(table, as_of, window)
        warnings: list[str] = []
        if len(base) < window:
            warnings.append(
                f"Apenas {len(base)} mês(es) fechado(s) disponível(is) (janela pedida: {window})."
            )

        liquidity = await self._liquidity(include_investments)
        income_sum, expense_sum = (
            float(base["income"].sum()),
            float(base["expense"].sum()),
        )
        savings_rate = (
            (income_sum - expense_sum) / income_sum if income_sum > 0 else None
        )

        burn_monthly = float(base["expense"].mean()) if len(base) else None
        std = float(base["expense"].std(ddof=1)) if len(base) >= 2 else None
        days = sum(p.days_in_month for p in base.index)
        burn_daily = expense_sum / days if days else None
        burn_stress = (
            burn_monthly + 2 * std
            if burn_monthly is not None and std is not None
            else None
        )
        if std is None:
            warnings.append(
                "Menos de 2 meses fechados: cenário de estresse (+2σ) indisponível."
            )

        def runway(burn: float | None) -> float | None:
            if not burn or burn <= 0:
                return None
            return max(liquidity, 0.0) / burn

        current = pd.Period(as_of, "M")
        cur = table.loc[current] if current in table.index else None
        cur_income = float(cur["income"]) if cur is not None else 0.0
        cur_expense = float(cur["expense"]) if cur is not None else 0.0
        cur_row = MonthlyFlow(
            month=str(current),
            income=_r0(cur_income),
            expense=_r0(cur_expense),
            savings=_r0(cur_income - cur_expense),
            savings_rate=_r((cur_income - cur_expense) / cur_income, 4)
            if cur_income > 0
            else None,
        )
        return CashflowResponse(
            as_of=as_of,
            window_months=window,
            months_used=[str(m) for m in base.index],
            monthly=[
                MonthlyFlow(
                    month=str(m),
                    income=_r0(r.income),
                    expense=_r0(r.expense),
                    savings=_r0(r.savings),
                    savings_rate=_r(r.savings_rate, 4),
                )
                for m, r in table.tail(window + 1).iterrows()
            ],
            current_month_to_date=cur_row,
            savings_rate_window=_r(savings_rate, 4),
            burn_rate_monthly=_r(burn_monthly),
            burn_rate_daily=_r(burn_daily),
            expense_std_monthly=_r(std),
            burn_rate_stress_monthly=_r(burn_stress),
            immediate_liquidity=_r0(liquidity),
            runway_months=_r(runway(burn_monthly), 2),
            runway_stress_months=_r(runway(burn_stress), 2),
            warnings=warnings,
        )

    async def anomalies(
        self, as_of: date, *, window: int = 6, threshold: float = 2.0
    ) -> AnomaliesResponse:
        flows = await self._flows(as_of)
        categories, months, warnings = category_zscores(
            flows, as_of, window=window, threshold=threshold
        )
        return AnomaliesResponse(
            as_of=as_of,
            baseline_months=months,
            z_threshold=threshold,
            categories=categories,
            warnings=warnings,
        )

    # -- 2. Monte Carlo -----------------------------------------------------
    async def monte_carlo(
        self,
        as_of: date,
        assumptions: MarketAssumptions,
        *,
        n_sims: int = 10_000,
        seed: int | None = None,
        horizons: Sequence[int] = (12, 24, 60),
    ) -> MonteCarloResponse:
        flows = await self._flows(as_of)
        base = complete_months(monthly_table(flows), as_of, 12)
        if len(base) < 3:
            raise InsufficientDataError(
                "São necessários ao menos 3 meses fechados de transações para estimar μ e σ da poupança."
            )
        warnings: list[str] = []
        savings_mu = float(base["savings"].mean())
        savings_sigma = float(base["savings"].std(ddof=1))

        positions = await self._positions()
        mu_p, sigma_p, defaulted = portfolio_return_and_vol(positions, assumptions)
        if defaulted:
            warnings.append(
                f"{defaulted} posição(ões) de renda fixa sem taxa legível: assumido 100% do CDI."
            )
        if not positions:
            warnings.append("Nenhum investimento ativo: carteira com valor zero.")

        loans = await self._loans()
        cards = await self._cards()
        cash = await self._liquidity(include_investments=False)
        portfolio_value = sum(max(p.net, 0.0) for p in positions)
        loans_total = sum(_num(loan.outstanding_balance) or 0.0 for loan in loans)
        card_debt = sum(self._card_used(c) for c in cards)

        max_h = max(horizons)
        principal = np.zeros(max_h)
        for loan in loans:
            n = loan.remaining_installments
            if (
                n is None
                and loan.total_installments is not None
                and loan.paid_installments is not None
            ):
                n = loan.total_installments - loan.paid_installments
            if not n:
                warnings.append(
                    f"Empréstimo {loan.id[:8]}: parcelas restantes desconhecidas; sem amortização modelada."
                )
                continue
            rate = (_num(loan.cet_monthly) or 0.0) / 100
            principal += principal_schedule(
                _num(loan.outstanding_balance) or 0.0, rate, int(n), max_h
            )

        paths = simulate_net_worth(
            initial_portfolio=portfolio_value,
            other_net=cash - loans_total - card_debt,
            savings_mu=savings_mu,
            savings_sigma=savings_sigma,
            portfolio_return_annual=mu_p,
            portfolio_vol_annual=sigma_p,
            debt_principal=principal,
            n_sims=n_sims,
            seed=seed,
        )
        out: list[WealthHorizon] = []
        for h in horizons:
            sample = paths[:, h - 1]
            p10, p50, p90 = np.percentile(sample, [10, 50, 90])
            out.append(
                WealthHorizon(
                    months=h,
                    p10=_r0(p10),
                    p50=_r0(p50),
                    p90=_r0(p90),
                    mean=_r0(sample.mean()),
                    prob_negative=_r0((sample < 0).mean(), 4),
                )
            )
        fan_percentiles = np.percentile(paths, [10, 50, 90], axis=0)
        fan = [
            FanPoint(
                months=t + 1,
                p10=_r0(fan_percentiles[0, t]),
                p50=_r0(fan_percentiles[1, t]),
                p90=_r0(fan_percentiles[2, t]),
            )
            for t in range(paths.shape[1])
        ]
        return MonteCarloResponse(
            as_of=as_of,
            n_sims=n_sims,
            seed=seed,
            fan=fan,
            inputs=MonteCarloInputs(
                initial_net_worth=_r0(portfolio_value + cash - loans_total - card_debt),
                portfolio_value=_r0(portfolio_value),
                cash=_r0(cash),
                loans_outstanding=_r0(loans_total),
                card_debt=_r0(card_debt),
                savings_mu_monthly=_r0(savings_mu),
                savings_sigma_monthly=_r0(savings_sigma),
                savings_months_used=len(base),
                portfolio_return_annual_net=_r0(mu_p, 4),
                portfolio_vol_annual=_r0(sigma_p, 4),
            ),
            horizons=out,
            warnings=warnings,
        )

    # -- 3. arbitragem dívida x investimento --------------------------------
    async def arbitrage(
        self, as_of: date, assumptions: MarketAssumptions
    ) -> ArbitrageResponse:
        warnings: list[str] = []
        loans = await self._loans()
        positions = [
            p for p in await self._positions() if p.asset_class == "RENDA_FIXA"
        ]
        if not positions:
            warnings.append(
                "Sem posições de renda fixa: renda variável e fundos não entram na comparação (risco/liquidez distintos)."
            )
        discount = assumptions.cdi_annual * (1 - FLAT_TAX_RATE)

        result: list[LoanArbitrage] = []
        for loan in loans:
            cet = _num(loan.cet_annual)
            outstanding = _num(loan.outstanding_balance) or 0.0
            if cet is None:
                warnings.append(f"Empréstimo {loan.id[:8]} sem CET: ignorado.")
                continue
            horizon = loan.remaining_installments
            if horizon is None and loan.due_date is not None:
                horizon = max(round((loan.due_date - as_of).days / DAYS_PER_MONTH), 1)
            horizon = int(min(max(horizon or 12, 1), 360))
            debt_rate = cet / 100

            rows: list[ArbitrageRow] = []
            for pos in positions:
                gross_return, from_data = expected_gross_return(pos, assumptions)
                notes: list[str] = []
                if not from_data:
                    notes.append("Taxa do papel indisponível: assumido 100% do CDI.")
                days_held = (
                    float((as_of - pos.issue_date).days) if pos.issue_date else 0.0
                )
                if pos.issue_date is None and not pos.tax_exempt:
                    notes.append(
                        "Data de aplicação desconhecida: IR calculado como se tivesse 0 dia."
                    )
                invested = pos.invested if pos.invested is not None else pos.gross
                ev = evaluate_debt_vs_investment(
                    gross_now=pos.gross,
                    invested=invested,
                    gross_annual_return=gross_return,
                    tax_exempt=pos.tax_exempt,
                    days_held_now=days_held,
                    horizon_months=horizon,
                    debt_annual_rate=debt_rate,
                    discount_annual=discount,
                )
                fraction = (
                    min(1.0, outstanding / ev["net_now"]) if ev["net_now"] > 0 else 0.0
                )
                net_annual = ev["net_annual_return"]
                rows.append(
                    ArbitrageRow(
                        investment_id=pos.id,
                        investment_name=pos.name,
                        subtype=pos.subtype,
                        gross_annual_return=_r0(gross_return, 4),
                        net_annual_return_if_kept=_r0(net_annual, 4),
                        cet_annual=_r0(debt_rate, 4),
                        spread_net_vs_cet=_r0(net_annual - debt_rate, 4)
                        if math.isfinite(net_annual)
                        else 0.0,
                        capital_considered=_r0(ev["net_now"] * fraction),
                        value_if_amortize=_r0(ev["value_amortize"] * fraction),
                        value_if_keep=_r0(ev["value_keep"] * fraction),
                        vpl_advantage_keep=_r0(ev["vpl_advantage_keep"] * fraction),
                        decision="MANTER_INVESTIDO"
                        if ev["vpl_advantage_keep"] > 0
                        else "AMORTIZAR_DIVIDA",
                        notes=notes,
                    )
                )
            to_liquidate = [r for r in rows if r.decision == "AMORTIZAR_DIVIDA"]
            suggested = (
                min(
                    to_liquidate, key=lambda r: r.net_annual_return_if_kept
                ).investment_id
                if to_liquidate
                else None
            )
            rows.sort(key=lambda r: r.vpl_advantage_keep)
            result.append(
                LoanArbitrage(
                    loan_id=loan.id,
                    loan_name=loan.product_name,
                    outstanding_balance=_r0(outstanding),
                    cet_annual=_r0(debt_rate, 4),
                    horizon_months=horizon,
                    suggested_source_to_liquidate=suggested,
                    rows=rows,
                )
            )
        return ArbitrageResponse(
            as_of=as_of,
            discount_rate_annual=_r0(discount, 4),
            loans=result,
            warnings=warnings,
        )

    # -- 3b. plano de quitação ------------------------------------------------
    async def loan_strategy(
        self, as_of: date, extra: float = 0.0
    ) -> LoanStrategyResponse:
        warnings: list[str] = []
        debts: list[DebtSpec] = []
        for loan in await self._loans():
            balance = _num(loan.outstanding_balance) or 0.0
            if balance <= 0:
                continue
            n = loan.remaining_installments
            if (
                n is None
                and loan.total_installments is not None
                and loan.paid_installments is not None
            ):
                n = loan.total_installments - loan.paid_installments
            label = loan.product_name or loan.contract_number or loan.id[:8]
            if not n or n <= 0:
                warnings.append(
                    f"{label}: parcelas restantes desconhecidas; fora do plano."
                )
                continue
            cet_monthly = _num(loan.cet_monthly)
            if cet_monthly is None:
                warnings.append(
                    f"{label}: sem CET; simulado com juros zero (subestima o custo)."
                )
            amortization = (
                "SAC"
                if "SAC" in (loan.amortization_scheduled or "").upper()
                else "PRICE"
            )
            debts.append(
                DebtSpec(
                    id=loan.id,
                    name=label,
                    balance=balance,
                    rate=(cet_monthly or 0.0) / 100,
                    n=int(n),
                    amortization=amortization,
                )
            )

        base_flows = complete_months(monthly_table(await self._flows(as_of)), as_of, 6)
        avg_income = float(base_flows["income"].mean()) if len(base_flows) else None
        if not debts:
            return LoanStrategyResponse(
                as_of=as_of, extra_monthly=extra, avg_monthly_income=_r(avg_income), total_balance=0.0,
                total_installments_now=0.0, loans=[], strategies=[], best_strategy=None, debt_curve=[],
                cost_curve=[], timeline=[], warnings=warnings + ["Nenhum empréstimo ativo com saldo e parcelas conhecidos."],
            )  # fmt: skip
        warnings.append(
            "Parcelas e juros são estimados a partir do saldo devedor, das parcelas restantes e do CET "
            "(Price ou SAC conforme o contrato); confira com os contratos."
        )

        baseline = simulate_payoff(debts, mode="baseline")
        avalanche = simulate_payoff(debts, extra=extra, mode="avalanche")
        snowball = simulate_payoff(debts, extra=extra, mode="snowball")
        names = {d.id: d.name for d in debts}

        def result(key: str, label: str, sim: PayoffSim) -> StrategyResult:
            order = sorted(
                sim.payoff_month.items(), key=lambda kv: (kv[1], names[kv[0]])
            )
            return StrategyResult(
                key=key,
                label=label,
                months_to_debt_free=sim.months if sim.finished else None,
                debt_free_on=add_months_label(as_of, sim.months)
                if sim.finished
                else None,
                total_interest=_r0(sim.total_interest),
                interest_saved=_r0(baseline.total_interest - sim.total_interest),
                months_saved=baseline.months - sim.months,
                payoff_order=[
                    PayoffStep(
                        loan_id=i, name=names[i], month=m, on=add_months_label(as_of, m)
                    )
                    for i, m in order
                ],
            )

        strategies = [
            result("baseline", "Só as parcelas atuais", baseline),
            result("avalanche", "Maior CET primeiro", avalanche),
            result("snowball", "Menor saldo primeiro", snowball),
        ]
        best = min(("avalanche", "snowball"), key=lambda k: (
            (avalanche if k == "avalanche" else snowball).total_interest,
            (avalanche if k == "avalanche" else snowball).months,
        ))  # fmt: skip

        plans: list[LoanPlan] = []
        for k, d in enumerate(debts):
            end = baseline.payoff_month.get(d.id)
            plans.append(
                LoanPlan(
                    loan_id=d.id,
                    name=d.name,
                    balance=_r0(d.balance),
                    cet_annual=_r(((1 + d.rate) ** 12 - 1) * 100, 4)
                    if d.rate
                    else None,
                    amortization=d.amortization,
                    remaining_installments=d.n,
                    installment_first=_r0(baseline.paid[0][k]),
                    installment_last=_r0(baseline.paid[end - 1][k]) if end else 0.0,
                    interest_remaining=_r0(baseline.interest_by_debt[d.id]),
                    ends_in_months=end,
                    ends_on=add_months_label(as_of, end) if end else None,
                )
            )

        length = max(
            len(baseline.remaining), len(avalanche.remaining), len(snowball.remaining)
        )

        def at(sim: PayoffSim, m: int) -> float:
            return _r0(sim.remaining[m]) if m < len(sim.remaining) else 0.0

        curve = [
            DebtPoint(
                month=m, on=add_months_label(as_of, m),
                baseline=at(baseline, m), avalanche=at(avalanche, m), snowball=at(snowball, m),
            )
            for m in range(length)
        ]  # fmt: skip

        def split(sim: PayoffSim, m: int) -> tuple[float, float]:
            """(juros, principal) pagos no mês m (1..); zeros depois de quitar."""
            if m > len(sim.paid):
                return 0.0, 0.0
            interest = sim.interest_by_month[m - 1]
            return _r0(interest), _r0(max(sum(sim.paid[m - 1]) - interest, 0.0))

        cost_curve = []
        for m in range(1, length):
            (bi, bp), (ai, ap), (si, sp) = (
                split(baseline, m),
                split(avalanche, m),
                split(snowball, m),
            )
            cost_curve.append(
                CostPoint(
                    month=m, on=add_months_label(as_of, m),
                    baseline_interest=bi, baseline_principal=bp,
                    avalanche_interest=ai, avalanche_principal=ap,
                    snowball_interest=si, snowball_principal=sp,
                )
            )  # fmt: skip
        timeline = [
            TimelinePoint(
                month=m + 1,
                on=add_months_label(as_of, m + 1),
                total=_r0(sum(row)),
                by_loan=[_r0(v) for v in row],
                commitment=_r(sum(row) / avg_income, 4)
                if avg_income and avg_income > 0
                else None,
            )
            for m, row in enumerate(baseline.paid)
        ]
        return LoanStrategyResponse(
            as_of=as_of,
            extra_monthly=extra,
            avg_monthly_income=_r(avg_income),
            total_balance=_r0(sum(d.balance for d in debts)),
            total_installments_now=_r0(sum(baseline.paid[0])),
            loans=plans,
            strategies=strategies,
            best_strategy=best,
            debt_curve=curve,
            cost_curve=cost_curve,
            timeline=timeline,
            warnings=warnings,
        )

    async def spending(
        self, year: int, month: int | None, today: date
    ) -> SpendingResponse:
        """Explorador de gastos de um ano (ou de um mês dele): categorias, comerciantes, dias e recorrentes."""
        year_rows = await self._session.execute(
            select(extract("year", Transaction.date)).distinct()
        )
        available = sorted(
            {int(r[0]) for r in year_rows.all() if r[0] is not None}, reverse=True
        )

        flows = await self._load_flows(datetime(year, 1, 1), datetime(year + 1, 1, 1))
        if month is None:
            start, end = date(year, 1, 1), date(year, 12, 31)
        else:
            start, end = (
                date(year, month, 1),
                date(year, month, monthrange(year, month)[1]),
            )
        warnings: list[str] = []
        empty = SpendingResponse(
            year=year, month=month, available_years=available, period_start=start, period_end=end,
            income_total=0.0, expense_total=0.0, savings_total=0.0, transaction_count=0, categories=[],
            top_merchants=[], top_transactions=[], days=[], weekdays=[], recurring=[],
            recurring_monthly_total=0.0, warnings=[f"Nenhuma transação importada em {year}."],
        )  # fmt: skip
        if flows.empty:
            return empty

        flows = flows.assign(
            key=flows["description"].map(merchant_key),
            month=flows["date"].dt.month,
            category=flows["category"].fillna(UNCATEGORIZED),
        )
        spend_year = flows[flows["expense"] != 0]
        recurring = detect_recurring(spend_year)

        in_period = flows if month is None else flows[flows["month"] == month]
        spend = in_period[in_period["expense"] != 0]
        if spend.empty and in_period["income"].sum() == 0:
            return empty.model_copy(
                update={
                    "warnings": [
                        f"Nenhuma transação em {MONTH_NAMES_PT[month - 1]}/{year}."
                    ]
                    if month
                    else empty.warnings
                }
            )

        income_total = float(in_period["income"].sum())
        expense_total = float(spend["expense"].sum())

        def to_tx(row: Any) -> SpendTransaction:
            return SpendTransaction(
                id=str(row.tx_id), date=row.date.date(), description=str(row.description or "(sem descrição)"),
                category=str(row.category), account=str(row.account_name), amount=_r0(row.expense),
            )  # fmt: skip

        def merchants(
            frame: pd.DataFrame, base: float, limit: int
        ) -> list[SpendMerchant]:
            grouped = (
                frame.groupby("key")["expense"]
                .agg(["sum", "count"])
                .sort_values("sum", ascending=False)
            )
            return [
                SpendMerchant(
                    name=str(k).title(), total=_r0(r["sum"]), count=int(r["count"]),
                    share=_r(r["sum"] / base, 4) if base > 0 else None,
                )
                for k, r in grouped.head(limit).iterrows()
                if r["sum"] > 0
            ]  # fmt: skip

        year_monthly = (
            spend_year.groupby(["category", "month"])["expense"].sum().unstack(fill_value=0.0)
            .reindex(columns=range(1, 13), fill_value=0.0)
        )  # fmt: skip
        categories: list[SpendCategory] = []
        for name, frame in spend.groupby("category"):
            total = float(frame["expense"].sum())
            if total <= 0:
                continue
            monthly = (
                year_monthly.loc[name].tolist()
                if name in year_monthly.index
                else [0.0] * 12
            )
            positive = frame[frame["expense"] > 0]
            categories.append(
                SpendCategory(
                    category=str(name), total=_r0(total),
                    share=_r(total / expense_total, 4) if expense_total > 0 else None,
                    count=len(positive), average=_r0(positive["expense"].mean()) if len(positive) else 0.0,
                    monthly=[_r0(v) for v in monthly],
                    merchants=merchants(frame, total, 10),
                    top_transactions=[to_tx(r) for r in positive.nlargest(6, "expense").itertuples()],
                )
            )  # fmt: skip
        categories.sort(key=lambda c: -c.total)

        daily = (
            spend.assign(day=spend["date"].dt.normalize())
            .groupby("day")["expense"]
            .agg(["sum", "count"])
        )
        days = [
            SpendDay(date=d.date(), expense=_r0(r["sum"]), count=int(r["count"]))
            for d, r in daily.iterrows()
        ]

        weekdays: list[SpendWeekday] = []
        first, last = spend["date"].min().normalize(), spend["date"].max().normalize()
        span = pd.date_range(first, last, freq="D")
        by_weekday = spend.groupby(spend["date"].dt.weekday)["expense"].agg(
            ["sum", "count"]
        )
        for wd in range(7):
            occurrences = int((span.weekday == wd).sum()) or 1
            total = float(by_weekday["sum"].get(wd, 0.0))
            weekdays.append(
                SpendWeekday(
                    weekday=wd, label=_WEEKDAYS_PT[wd], total=_r0(total),
                    count=int(by_weekday["count"].get(wd, 0)), average_per_day=_r0(total / occurrences),
                )
            )  # fmt: skip

        uncategorized = next(
            (c.total for c in categories if c.category == UNCATEGORIZED), 0.0
        )
        if expense_total > 0 and uncategorized / expense_total >= 0.15:
            warnings.append(
                f"{uncategorized / expense_total:.0%} das despesas estão sem categoria."
            )
        if recurring:
            warnings.append(
                "Gastos recorrentes são detectados pelo nome do estabelecimento e pela regularidade do valor; "
                "confira a lista antes de cancelar algo."
            )

        positive_spend = spend[spend["expense"] > 0]
        # "Avulsos": fora os gastos fixos (aluguel, assinaturas), que já têm bloco próprio e dominariam a lista.
        recurring_names = {r.name for r in recurring}
        one_off = positive_spend[
            ~positive_spend["key"].map(lambda k: str(k).title() in recurring_names)
        ]
        return SpendingResponse(
            year=year, month=month, available_years=available, period_start=start, period_end=end,
            income_total=_r0(income_total), expense_total=_r0(expense_total),
            savings_total=_r0(income_total - expense_total), transaction_count=len(positive_spend),
            categories=categories, top_merchants=merchants(spend, expense_total, 15),
            top_transactions=[to_tx(r) for r in one_off.nlargest(30, "expense").itertuples()],
            days=days, weekdays=weekdays, recurring=recurring[:20],
            recurring_monthly_total=_r0(sum(r.monthly_amount for r in recurring)), warnings=warnings,
        )  # fmt: skip

    # -- 4. risco de crédito ------------------------------------------------
    async def credit_risk(
        self, as_of: date, *, include_investments: bool = False
    ) -> CreditRiskResponse:
        warnings: list[str] = []
        cards = await self._cards()
        card_rows: list[CardUtilization] = []
        for card in cards:
            limit = _num(card.credit_limit) or 0.0
            used = self._card_used(card)
            card_rows.append(
                CardUtilization(
                    account_id=card.id,
                    name=card.marketing_name or card.name,
                    credit_limit=_r0(limit),
                    used=_r0(used),
                    utilization=_r(used / limit, 4) if limit > 0 else None,
                )
            )
        limit_total = sum(c.credit_limit for c in card_rows)
        used_total = sum(c.used for c in card_rows)
        utilization = used_total / limit_total if limit_total > 0 else None

        loans = await self._loans()
        loans_total = sum(_num(loan.outstanding_balance) or 0.0 for loan in loans)
        total_debt = loans_total + used_total
        liquidity = await self._liquidity(include_investments)
        coverage = liquidity / total_debt if total_debt > 0 else None

        installments = 0.0
        for loan in loans:
            n = loan.remaining_installments
            if not n:
                warnings.append(
                    f"Empréstimo {loan.id[:8]}: parcelas restantes desconhecidas; parcela não estimada."
                )
                continue
            installments += price_payment(
                _num(loan.outstanding_balance) or 0.0,
                (_num(loan.cet_monthly) or 0.0) / 100,
                int(n),
            )
        if loans:
            warnings.append(
                "Parcelas estimadas pela tabela Price a partir do saldo devedor e do CET mensal."
            )

        base = complete_months(monthly_table(await self._flows(as_of)), as_of, 6)
        avg_income = float(base["income"].mean()) if len(base) else None
        commitment = (
            installments / avg_income if avg_income and avg_income > 0 else None
        )

        flags: list[str] = []
        if utilization is not None and utilization >= 0.30:
            flags.append("Utilização do limite de crédito >= 30%")
        if coverage is not None and coverage < 1.0:
            flags.append("Liquidez imediata menor que a dívida total")
        if commitment is not None and commitment >= 0.30:
            flags.append("Parcelas comprometem >= 30% da renda média")

        return CreditRiskResponse(
            as_of=as_of,
            credit_limit_total=_r0(limit_total),
            credit_used_total=_r0(used_total),
            utilization_total=_r(utilization, 4),
            cards=card_rows,
            immediate_liquidity=_r0(liquidity),
            loans_outstanding=_r0(loans_total),
            total_debt=_r0(total_debt),
            debt_coverage_ratio=_r(coverage, 4),
            monthly_installments_estimated=_r0(installments),
            avg_monthly_income=_r(avg_income),
            income_commitment=_r(commitment, 4),
            flags=flags,
            warnings=warnings,
        )
