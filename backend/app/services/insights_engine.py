"""Análises adicionais sobre o mesmo banco: caixa futuro, ritmo do mês, variação, estrutura de gastos,
concentração, carteira, parcelados, rede do dinheiro (grafo) e histórico de patrimônio.

Herda de ``QuantEngine`` para reaproveitar o carregamento e a classificação de fluxos (mesmas regras de
"o que é gasto"), as posições, empréstimos e cartões. Tudo é derivado dos dados já importados.
"""

import logging
import math
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Account, Investment, NetWorthSnapshot, Transaction
from app.schemas.insights import (
    AuditExample,
    AuditGroup,
    CashEvent,
    CashForecastResponse,
    CashPoint,
    CategoryChange,
    CategoryChangeResponse,
    ConcentrationResponse,
    FlowAuditResponse,
    GraphLink,
    GraphNode,
    InstallmentMonth,
    InstallmentPurchase,
    InstallmentsResponse,
    MaturityBucket,
    MoneyGraphResponse,
    NetWorthPoint,
    NetWorthResponse,
    PaceResponse,
    ParetoItem,
    ParetoSeries,
    PortfolioClass,
    PortfolioPosition,
    PortfolioResponse,
    StructureMonth,
    StructureResponse,
)
from app.schemas.quant import MarketAssumptions
from app.services.flow_rules import REASON_LABELS
from app.services.quant_engine import (
    MONTH_NAMES_PT,
    DebtSpec,
    QuantEngine,
    _num,
    _r,
    _r0,
    add_months_label,
    expected_gross_return,
    merchant_key,
    recurring_groups,
    simulate_payoff,
)

logger = logging.getLogger(__name__)

Z_P10_P90: Final = 1.2816  # normal: p10/p90 = média ∓ 1,2816 σ
VARIABLE_WINDOW_DAYS: Final = 90
RECURRING_ACTIVE_DAYS: Final = 45  # um fixo "morre" se não aparece há mais que isso
CLASS_LABELS: Final = {
    "RENDA_FIXA": "Renda fixa",
    "RENDA_VARIAVEL": "Renda variável",
    "FUNDOS": "Fundos",
    "OUTROS": "Outros",
}


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + delta
    return total // 12, total % 12 + 1


def _clamp_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _label(year: int, month: int) -> str:
    return f"{MONTH_NAMES_PT[month - 1][:3].lower()}/{year}"


def _dt(value: date) -> datetime:
    return datetime.combine(value, time.min)


def pareto(totals: pd.Series, limit: int = 20) -> ParetoSeries:
    """Curva de Pareto: itens do maior para o menor, com participação e acumulado."""
    positive = totals[totals > 0].sort_values(ascending=False)
    grand = float(positive.sum())
    if grand <= 0:
        return ParetoSeries(items=[], count=0, n_for_50=0, n_for_80=0)
    cumulative = positive.cumsum() / grand
    items = [
        ParetoItem(
            name=str(name),
            total=_r0(value),
            share=_r0(value / grand, 4),
            cumulative=_r0(cum, 4),
        )
        for (name, value), cum in zip(
            positive.head(limit).items(), cumulative.head(limit), strict=True
        )
    ]
    n50 = int((cumulative < 0.5).sum()) + 1
    n80 = int((cumulative < 0.8).sum()) + 1
    return ParetoSeries(items=items, count=len(positive), n_for_50=n50, n_for_80=n80)


class InsightsEngine(QuantEngine):
    # -- carga ----------------------------------------------------------------
    async def _keyed(
        self, since: datetime, until: datetime | None = None
    ) -> pd.DataFrame:
        """Fluxos classificados, com a chave do comerciante (``key``) e a conta (``account_name``)."""
        flows = await self._load_flows(since, until)
        if flows.empty:
            return pd.DataFrame(
                {
                    "date": pd.to_datetime([]),
                    "category": pd.Series(dtype=str),
                    "income": pd.Series(dtype=float),
                    "expense": pd.Series(dtype=float),
                    "description": pd.Series(dtype=str),
                    "account_name": pd.Series(dtype=str),
                    "tx_id": pd.Series(dtype=str),
                    "key": pd.Series(dtype=str),
                }
            )
        return flows.assign(key=flows["description"].map(merchant_key))

    # -- 1. projeção de caixa -----------------------------------------------------
    async def cash_forecast(self, as_of: date, days: int = 60) -> CashForecastResponse:
        """Saldo diário previsto: fixos e salário nos dias de sempre, parcelas, fatura atual e gasto variável.

        O gasto variável é a média diária dos últimos 90 dias fora os fixos (com os dias extremos limitados
        ao percentil 95); a faixa é p10-p90 de uma soma de dias independentes. Compras no cartão entram no
        dia da compra (visão conservadora) e a fatura já aberta sai no vencimento.
        """
        warnings: list[str] = []
        start_balance = await self._liquidity(include_investments=False)
        flows = await self._keyed(_dt(as_of - timedelta(days=200)), _dt(as_of))
        horizon_end = as_of + timedelta(days=days)
        events: list[CashEvent] = []

        # parcelas de empréstimos (PRICE/SAC, a partir do CET) no dia de vencimento conhecido
        loan_installments: list[float] = []
        for loan in await self._loans():
            n = loan.remaining_installments
            if (
                n is None
                and loan.total_installments is not None
                and loan.paid_installments is not None
            ):
                n = loan.total_installments - loan.paid_installments
            balance = _num(loan.outstanding_balance) or 0.0
            if not n or n <= 0 or balance <= 0:
                continue
            label = loan.product_name or loan.contract_number or loan.id[:8]
            amortization = (
                "SAC"
                if "SAC" in (loan.amortization_scheduled or "").upper()
                else "PRICE"
            )
            spec = DebtSpec(
                loan.id,
                label,
                balance,
                (_num(loan.cet_monthly) or 0.0) / 100,
                int(n),
                amortization,
            )
            schedule = [row[0] for row in simulate_payoff([spec], mode="baseline").paid]
            loan_installments.append(spec.first_installment)
            due_day = (
                loan.first_installment_due_date.day
                if loan.first_installment_due_date
                else None
            )
            if due_day is None:
                due_day = 10
                warnings.append(
                    f"{label}: dia de vencimento desconhecido; assumido dia 10."
                )
            upcoming, year, month = 0, as_of.year, as_of.month
            while upcoming < len(schedule):
                due = _clamp_day(year, month, due_day)
                if due > horizon_end:
                    break
                if due > as_of:
                    events.append(
                        CashEvent(
                            date=due,
                            label=f"Parcela {label}",
                            amount=-_r0(schedule[upcoming]),
                            kind="loan",
                        )
                    )
                    upcoming += 1
                year, month = _shift_month(year, month, 1)

        # fatura do cartão já aberta
        for card in await self._cards():
            used = self._card_used(card)
            if used <= 0:
                continue
            due = card.credit_balance_due_date
            if due is None or due <= as_of:
                day = due.day if due else 10
                year, month = (
                    (as_of.year, as_of.month)
                    if _clamp_day(as_of.year, as_of.month, day) > as_of
                    else _shift_month(as_of.year, as_of.month, 1)
                )
                due = _clamp_day(year, month, day)
                if card.credit_balance_due_date is None:
                    warnings.append(
                        f"{card.name}: vencimento da fatura desconhecido; assumido dia {day}."
                    )
            if due <= horizon_end:
                events.append(
                    CashEvent(
                        date=due,
                        label=f"Fatura {card.name}",
                        amount=-_r0(used),
                        kind="card",
                    )
                )

        # fixos e salário (só os que ainda aparecem)
        def active(group: dict[str, Any]) -> bool:
            return (as_of - group["last_seen"]).days <= RECURRING_ACTIVE_DAYS

        expense_groups = [
            g
            for g in recurring_groups(flows[flows["expense"] > 0], "expense")
            if active(g)
        ]
        income_groups = [
            g
            for g in recurring_groups(flows[flows["income"] > 0], "income")
            if active(g)
        ]
        # parcela de empréstimo que já aparece como débito recorrente não pode entrar duas vezes
        expense_groups = [
            g
            for g in expense_groups
            if not any(
                abs(g["median"] - inst) <= 0.08 * inst for inst in loan_installments
            )
        ]
        for groups, sign, kind in (
            (expense_groups, -1.0, "recurring"),
            (income_groups, 1.0, "income"),
        ):
            for g in groups:
                year, month = as_of.year, as_of.month
                for _ in range(days // 28 + 3):
                    due = _clamp_day(year, month, g["day"])
                    if as_of < due <= horizon_end:
                        events.append(
                            CashEvent(
                                date=due,
                                label=str(g["key"]).title(),
                                amount=sign * _r0(g["median"]),
                                kind=kind,
                            )
                        )  # type: ignore[arg-type]
                    year, month = _shift_month(year, month, 1)

        # gasto variável diário (fora os fixos)
        fixed_keys = {
            g["key"] for g in recurring_groups(flows[flows["expense"] > 0], "expense")
        }
        window_start = as_of - timedelta(days=VARIABLE_WINDOW_DAYS)
        recent = flows[
            (flows["date"] >= _dt(window_start)) & ~flows["key"].isin(fixed_keys)
        ]
        daily = (
            recent.groupby(recent["date"].dt.normalize())["expense"].sum()
            if not recent.empty
            else pd.Series(dtype=float)
        )
        index = pd.date_range(window_start, as_of - timedelta(days=1), freq="D")
        daily = daily.reindex(index, fill_value=0.0)
        if len(daily) >= 14 and daily.sum() > 0:
            # limita os dias extremos pelo P95 dos dias COM gasto (um P95 sobre todos os dias zeraria quem gasta em poucos dias)
            daily = daily.clip(upper=float(daily[daily > 0].quantile(0.95)))
            mu, sigma = float(daily.mean()), float(daily.std(ddof=1))
        else:
            mu, sigma = 0.0, 0.0
            warnings.append(
                "Poucos dados recentes de gasto variável; a projeção considera apenas contas e parcelas."
            )

        by_day: dict[date, float] = {}
        for event in events:
            by_day[event.date] = by_day.get(event.date, 0.0) + event.amount
        points: list[CashPoint] = []
        balance = start_balance
        for i in range(1, days + 1):
            day = as_of + timedelta(days=i)
            balance += by_day.get(day, 0.0) - mu
            band = Z_P10_P90 * sigma * math.sqrt(i)
            points.append(
                CashPoint(
                    date=day,
                    base=_r0(balance),
                    low=_r0(balance - band),
                    high=_r0(balance + band),
                )
            )

        lowest = min(points, key=lambda p: p.base)
        negative = lambda series: next((p.date for p in points if series(p) < 0), None)
        warnings.append(
            "Projeção estimada: fixos e salário no dia habitual, gasto variável pela média recente. "
            "Receitas e gastos fora do padrão não estão incluídos."
        )
        return CashForecastResponse(
            as_of=as_of, days=days, starting_balance=_r0(start_balance), ending_balance=points[-1].base,
            min_balance=lowest.base, min_date=lowest.date,
            first_negative_base=negative(lambda p: p.base), first_negative_low=negative(lambda p: p.low),
            daily_variable_spend=_r0(mu), points=points, events=sorted(events, key=lambda e: e.date), warnings=warnings,
        )  # fmt: skip

    # -- 2. ritmo do mês ----------------------------------------------------------
    async def pace(self, as_of: date) -> PaceResponse:
        """Gasto acumulado do mês corrente contra o mês passado e a média dos meses fechados."""
        warnings: list[str] = []
        first_year, first_month = _shift_month(as_of.year, as_of.month, -6)
        flows = await self._load_flows(
            _dt(_month_start(first_year, first_month)), _dt(as_of + timedelta(days=1))
        )
        daily = (
            flows.groupby(flows["date"].dt.normalize())["expense"].sum()
            if not flows.empty
            else pd.Series(dtype=float)
        )

        def cumulative(year: int, month: int) -> np.ndarray:
            length = monthrange(year, month)[1]
            values = np.array(
                [
                    float(daily.get(pd.Timestamp(year, month, d), 0.0))
                    for d in range(1, length + 1)
                ],
                dtype=float,
            )
            running = np.cumsum(values)
            return np.concatenate([running, np.full(31 - length, running[-1])])

        closed: list[tuple[int, int, np.ndarray]] = []
        for back in range(1, 7):
            year, month = _shift_month(as_of.year, as_of.month, -back)
            curve = cumulative(year, month)
            if curve[-1] != 0:
                closed.append((year, month, curve))
        today = as_of.day
        current_curve = cumulative(as_of.year, as_of.month)
        current: list[float | None] = [
            _r0(v) if d + 1 <= today else None for d, v in enumerate(current_curve)
        ]
        spent = _r0(current_curve[today - 1])

        if not closed:
            warnings.append("Sem meses fechados para comparar.")
            return PaceResponse(
                as_of=as_of, month_label=f"{as_of.year}-{as_of.month:02d}", day_of_month=today, days=list(range(1, 32)),
                current=current, previous=[0.0] * 31, average=[0.0] * 31, previous_label=None, months_in_average=0,
                spent_to_date=spent, previous_to_date=None, average_to_date=None, projected_end=None,
                typical_end=None, previous_total=None, warnings=warnings,
            )  # fmt: skip
        previous_year, previous_month, previous_curve = closed[0]
        average_curve = np.mean([c for _, _, c in closed], axis=0)
        average_to_date = float(average_curve[today - 1])
        projected = (
            spent * float(average_curve[-1]) / average_to_date
            if average_to_date > 0
            else None
        )
        return PaceResponse(
            as_of=as_of, month_label=f"{as_of.year}-{as_of.month:02d}", day_of_month=today, days=list(range(1, 32)),
            current=current, previous=[_r0(v) for v in previous_curve], average=[_r0(v) for v in average_curve],
            previous_label=_label(previous_year, previous_month), months_in_average=len(closed),
            spent_to_date=spent, previous_to_date=_r0(previous_curve[today - 1]), average_to_date=_r0(average_to_date),
            projected_end=_r(projected), typical_end=_r0(average_curve[-1]), previous_total=_r0(previous_curve[-1]),
            warnings=warnings,
        )  # fmt: skip

    # -- 3. variação entre períodos -------------------------------------------------
    async def category_change(
        self, year: int, month: int, mode: str = "previous_month"
    ) -> CategoryChangeResponse:
        """Quanto cada categoria subiu ou caiu contra o mês anterior, o mesmo mês do ano passado ou a média de 6 meses."""
        warnings: list[str] = []
        flows = await self._load_flows(
            _dt(date(year - 1, month, 1)),
            _dt(_month_end(year, month) + timedelta(days=1)),
        )
        flows = flows[flows["expense"] != 0] if not flows.empty else flows

        def totals(y: int, m: int) -> pd.Series:
            if flows.empty:
                return pd.Series(dtype=float)
            in_month = flows[
                (flows["date"].dt.year == y) & (flows["date"].dt.month == m)
            ]
            return in_month.groupby("category")["expense"].sum()

        current = totals(year, month)
        if mode == "previous_year":
            base = totals(year - 1, month)
            base_label = _label(year - 1, month)
        elif mode == "average_6m":
            frames = [totals(*_shift_month(year, month, -k)) for k in range(1, 7)]
            frames = [f for f in frames if not f.empty]
            base = (
                pd.concat(frames, axis=1).fillna(0.0).mean(axis=1)
                if frames
                else pd.Series(dtype=float)
            )
            base_label = f"média dos {len(frames)} meses anteriores"
            if len(frames) < 6:
                warnings.append(
                    f"Só há {len(frames)} meses anteriores com gastos para compor a média."
                )
        else:
            base = totals(*_shift_month(year, month, -1))
            base_label = _label(*_shift_month(year, month, -1))
        if base.empty:
            warnings.append("Não há gastos no período de comparação.")

        rows = [
            CategoryChange(
                category=str(name), current=_r0(current.get(name, 0.0)), base=_r0(base.get(name, 0.0)),
                delta=_r0(current.get(name, 0.0) - base.get(name, 0.0)),
                delta_pct=_r((current.get(name, 0.0) - base.get(name, 0.0)) / base.get(name, 0.0), 4)
                if base.get(name, 0.0) > 0 else None,
            )
            for name in sorted(set(current.index) | set(base.index))
        ]  # fmt: skip
        rows.sort(key=lambda r: -abs(r.delta))
        return CategoryChangeResponse(
            year=year, month=month, mode=mode,  # type: ignore[arg-type]
            current_label=_label(year, month), base_label=base_label,
            current_total=_r0(current.sum()), base_total=_r0(base.sum()), rows=rows, warnings=warnings,
        )  # fmt: skip

    # -- 4. fixo x variável x avulso ----------------------------------------------------
    async def structure(self, year: int) -> StructureResponse:
        """Quanto do gasto de cada mês já estava "travado" (fixo), é do dia a dia (variável) ou foi pontual (avulso)."""
        flows = await self._keyed(_dt(date(year, 1, 1)), _dt(date(year + 1, 1, 1)))
        spend = flows[flows["expense"] != 0]
        fixed_groups = recurring_groups(spend, "expense")
        fixed_keys = {g["key"] for g in fixed_groups}
        other = spend[~spend["key"].isin(fixed_keys)]
        tickets = other.loc[other["expense"] > 0, "expense"]
        threshold = max(300.0, 5.0 * float(tickets.median())) if len(tickets) else 300.0

        fixed_mask = spend["key"].isin(fixed_keys)
        one_off_mask = ~fixed_mask & (spend["expense"] >= threshold)
        months: list[StructureMonth] = []
        for m in range(1, 13):
            in_month = spend["date"].dt.month == m
            fixed = float(spend.loc[in_month & fixed_mask, "expense"].sum())
            one_off = float(spend.loc[in_month & one_off_mask, "expense"].sum())
            variable = float(
                spend.loc[in_month & ~fixed_mask & ~one_off_mask, "expense"].sum()
            )
            months.append(
                StructureMonth(
                    month=m,
                    fixed=_r0(fixed),
                    variable=_r0(variable),
                    one_off=_r0(one_off),
                    total=_r0(fixed + variable + one_off),
                )
            )
        fixed_total = sum(x.fixed for x in months)
        variable_total = sum(x.variable for x in months)
        one_off_total = sum(x.one_off for x in months)
        grand = fixed_total + variable_total + one_off_total
        warnings = [] if grand > 0 else [f"Nenhum gasto em {year}."]
        if fixed_groups:
            warnings.append(
                "Fixo = gasto que se repete todo mês com valor parecido; avulso = lançamento grande fora do padrão."
            )
        return StructureResponse(
            year=year, months=months, fixed_total=_r0(fixed_total), variable_total=_r0(variable_total),
            one_off_total=_r0(one_off_total), fixed_share=_r(fixed_total / grand, 4) if grand > 0 else None,
            one_off_threshold=_r0(threshold), fixed_items=sorted(str(g["key"]).title() for g in fixed_groups),
            warnings=warnings,
        )  # fmt: skip

    # -- 5. concentração ----------------------------------------------------------------
    async def concentration(
        self, year: int, month: int | None
    ) -> ConcentrationResponse:
        start = _month_start(year, month or 1)
        end = _month_end(year, month) if month else _month_end(year, 12)
        flows = await self._keyed(_dt(start), _dt(end + timedelta(days=1)))
        spend = flows[flows["expense"] > 0]
        categories = (
            pareto(spend.groupby("category")["expense"].sum())
            if not spend.empty
            else pareto(pd.Series(dtype=float))
        )
        merchants = (
            pareto(
                spend.groupby("key")["expense"].sum().rename(lambda k: str(k).title())
            )
            if not spend.empty
            else pareto(pd.Series(dtype=float))
        )
        return ConcentrationResponse(
            year=year, month=month, expense_total=_r0(spend["expense"].sum()), categories=categories,
            merchants=merchants, warnings=[] if len(spend) else ["Nenhum gasto neste período."],
        )  # fmt: skip

    # -- 6. carteira ----------------------------------------------------------------------
    async def portfolio(
        self, as_of: date, assumptions: MarketAssumptions
    ) -> PortfolioResponse:
        warnings: list[str] = []
        positions = await self._positions()
        details = {
            inv.id: inv
            for inv in (
                await self._session.scalars(
                    select(Investment).where(Investment.is_active.is_(True))
                )
            ).all()
        }
        total = sum(max(p.net, 0.0) for p in positions)
        cdi_pct = assumptions.cdi_annual * 100

        out: list[PortfolioPosition] = []
        by_class: dict[str, float] = {}
        buckets: dict[str, list[tuple[str, float]]] = {}
        for pos in positions:
            inv = details.get(pos.id)
            value = max(pos.net, 0.0)
            by_class[pos.asset_class] = by_class.get(pos.asset_class, 0.0) + value
            name = pos.name or (inv.code if inv else None) or pos.id[:8]
            if pos.last_twelve_months_rate is not None:
                rate, source = pos.last_twelve_months_rate, "realizada_12m"
            else:
                gross, from_data = expected_gross_return(pos, assumptions)
                rate, source = gross * 100, "contratada" if from_data else "estimada"
            due = inv.due_date if inv else None
            profit = _num(inv.profit) if inv else None
            out.append(
                PortfolioPosition(
                    id=pos.id, name=name, subtype=pos.subtype, asset_class=pos.asset_class, value=_r0(value),
                    share=_r0(value / total, 4) if total > 0 else 0.0, indexer=pos.indexer, rate_pct=_r(rate),
                    rate_source=source,  # type: ignore[arg-type]
                    spread_vs_cdi=_r(rate - cdi_pct), profit=_r(profit),
                    profit_pct=_r(profit / pos.invested * 100) if profit is not None and pos.invested else None,
                    due_date=due, days_to_due=(due - as_of).days if due else None,
                )
            )  # fmt: skip
            if pos.asset_class == "RENDA_FIXA" and due and due >= as_of:
                buckets.setdefault(f"{due.year}-{due.month:02d}", []).append(
                    (name, value)
                )

        if any(p.rate_source == "estimada" for p in out):
            warnings.append(
                "Posições sem taxa legível foram estimadas a 100% do CDI/retorno padrão da classe."
            )
        if not positions:
            warnings.append("Nenhum investimento ativo.")
        out.sort(key=lambda p: -p.value)
        maturities = [
            MaturityBucket(
                month=m,
                value=_r0(sum(v for _, v in items)),
                items=[n for n, _ in items],
            )
            for m, items in sorted(buckets.items())
        ]
        limit_year, limit_month = _shift_month(as_of.year, as_of.month, 12)
        limit = f"{limit_year}-{limit_month:02d}"
        return PortfolioResponse(
            as_of=as_of, total=_r0(total), cdi_annual_pct=_r0(cdi_pct),
            by_class=[
                PortfolioClass(asset_class=k, label=CLASS_LABELS.get(k, k), value=_r0(v), share=_r0(v / total, 4) if total > 0 else 0.0)
                for k, v in sorted(by_class.items(), key=lambda kv: -kv[1]) if v > 0
            ],
            positions=out, maturities=maturities,
            matures_12m=_r0(sum(b.value for b in maturities if b.month <= limit)), warnings=warnings,
        )  # fmt: skip

    # -- 7. parcelados no cartão ---------------------------------------------------------------
    async def installments(self, as_of: date) -> InstallmentsResponse:
        """Parcelas de compras já feitas que ainda vão cair nos próximos meses."""
        since = _dt(date(*_shift_month(as_of.year, as_of.month, -13), 1))
        rows = (
            await self._session.execute(
                select(
                    Transaction.date,
                    Transaction.description,
                    Transaction.amount,
                    Transaction.account_id,
                    Transaction.installment_number,
                    Transaction.total_installments,
                    Account.name,
                )
                .join(Account, Account.id == Transaction.account_id)
                .where(
                    Transaction.total_installments > 1,
                    Transaction.installment_number.is_not(None),
                    Transaction.date >= since,
                )
            )
        ).all()
        latest: dict[tuple[str, str, int, int], Any] = {}
        for row in rows:
            key = (
                row.account_id,
                merchant_key(row.description),
                int(row.total_installments),
                round(abs(float(row.amount))),
            )
            if key not in latest or (row.installment_number, row.date) > (
                latest[key].installment_number,
                latest[key].date,
            ):
                latest[key] = row

        purchases: list[InstallmentPurchase] = []
        by_month: dict[str, list[float]] = {}
        for row in latest.values():
            remaining = int(row.total_installments) - int(row.installment_number)
            elapsed = max(
                (as_of.year - row.date.year) * 12 + (as_of.month - row.date.month), 0
            )
            future = remaining - elapsed
            if future <= 0:
                continue
            amount = abs(float(row.amount))
            for j in range(1, future + 1):
                by_month.setdefault(add_months_label(as_of, j), []).append(amount)
            purchases.append(
                InstallmentPurchase(
                    description=str(row.description or "(sem descrição)"),
                    account=str(row.name),
                    amount=_r0(amount),
                    number=int(row.installment_number),
                    total=int(row.total_installments),
                    remaining=future,
                    remaining_total=_r0(amount * future),
                    ends_on=add_months_label(as_of, future),
                )
            )
        purchases.sort(key=lambda p: -p.remaining_total)
        months = [
            InstallmentMonth(month=m, total=_r0(sum(v)), count=len(v))
            for m, v in sorted(by_month.items())
        ]
        warnings = (
            []
            if purchases
            else [
                "Nenhuma compra parcelada com parcelas futuras. Se você parcela no cartão, confira se a sua fonte informa a parcela atual e o total."
            ]
        )
        return InstallmentsResponse(
            as_of=as_of, months=months, purchases=purchases, total_committed=_r0(sum(p.remaining_total for p in purchases)),
            next_month=months[0].total if months else 0.0, warnings=warnings,
        )  # fmt: skip

    # -- 8. rede do dinheiro (grafo) --------------------------------------------------------------
    async def money_graph(self, year: int, month: int | None) -> MoneyGraphResponse:
        """Grafo receita -> conta -> categoria -> estabelecimento (valores do período, só o que é relevante)."""
        start = _month_start(year, month or 1)
        end = _month_end(year, month) if month else _month_end(year, 12)
        flows = await self._keyed(_dt(start), _dt(end + timedelta(days=1)))
        income = flows[flows["income"] > 0]
        spend = flows[flows["expense"] > 0]
        if spend.empty and income.empty:
            return MoneyGraphResponse(
                year=year,
                month=month,
                nodes=[],
                links=[],
                expense_total=0.0,
                income_total=0.0,
                warnings=["Nenhuma transação neste período."],
            )

        nodes: dict[str, GraphNode] = {}
        link_values: dict[tuple[str, str], float] = {}

        def add_node(node_id: str, label: str, kind: str, value: float) -> None:
            if node_id in nodes:
                nodes[node_id].value += value
            else:
                nodes[node_id] = GraphNode(
                    id=node_id, label=label, kind=kind, value=value
                )  # type: ignore[arg-type]

        def add_link(source: str, target: str, value: float) -> None:
            link_values[(source, target)] = (
                link_values.get((source, target), 0.0) + value
            )

        top_income = income.groupby("key")["income"].sum().nlargest(4).index
        for row in income.itertuples():
            label = str(row.key).title() if row.key in top_income else "Outras receitas"
            income_id = f"inc:{label}"
            account_id = f"acc:{row.account_name}"
            add_node(income_id, label, "income", float(row.income))
            add_node(account_id, str(row.account_name), "account", 0.0)
            add_link(income_id, account_id, float(row.income))

        top_categories = list(
            spend.groupby("category")["expense"].sum().nlargest(10).index
        )
        spend = spend.assign(
            group=spend["category"].where(
                spend["category"].isin(top_categories), "Outras categorias"
            )
        )
        for (account_name, group), value in (
            spend.groupby(["account_name", "group"])["expense"].sum().items()
        ):
            account_id, category_id = f"acc:{account_name}", f"cat:{group}"
            add_node(account_id, str(account_name), "account", float(value))
            add_node(category_id, str(group), "category", float(value))
            add_link(account_id, category_id, float(value))

        for group in list(spend.groupby("group")["expense"].sum().nlargest(8).index):
            if group == "Outras categorias":
                continue
            merchants = (
                spend[spend["group"] == group]
                .groupby("key")["expense"]
                .sum()
                .nlargest(3)
            )
            for key, value in merchants.items():
                merchant_id = f"mer:{group}|{key}"
                add_node(merchant_id, str(key).title(), "merchant", float(value))
                add_link(f"cat:{group}", merchant_id, float(value))

        # conta só de entrada (sem gasto) fica com o valor recebido, para ter tamanho no grafo
        for node in nodes.values():
            if node.kind == "account" and node.value == 0.0:
                node.value = sum(v for (s, t), v in link_values.items() if t == node.id)
        for node in nodes.values():
            node.value = _r0(node.value)
        return MoneyGraphResponse(
            year=year, month=month, nodes=list(nodes.values()),
            links=[GraphLink(source=s, target=t, value=_r0(v)) for (s, t), v in link_values.items()],
            expense_total=_r0(spend["expense"].sum()), income_total=_r0(income["income"].sum()), warnings=[],
        )  # fmt: skip

    # -- 9. patrimônio ao longo do tempo ------------------------------------------------------------
    async def _net_worth_components(self) -> dict[str, float]:
        positions = await self._positions()
        loans = await self._loans()
        cards = await self._cards()
        liquidity = await self._liquidity(include_investments=False)
        investments = sum(max(p.net, 0.0) for p in positions)
        loans_total = sum(_num(loan.outstanding_balance) or 0.0 for loan in loans)
        card_debt = sum(self._card_used(c) for c in cards)
        return {
            "net_worth": liquidity + investments - loans_total - card_debt,
            "liquidity": liquidity, "investments": investments, "loans": loans_total, "card_debt": card_debt,
        }  # fmt: skip

    async def record_snapshot(self, day: date) -> NetWorthPoint:
        """Grava (ou atualiza) a foto do patrimônio do dia. Chamado a cada sincronização."""
        parts = await self._net_worth_components()
        snapshot = await self._session.get(NetWorthSnapshot, day)
        values = {k: Decimal(str(round(v, 2))) for k, v in parts.items()}
        if snapshot is None:
            self._session.add(NetWorthSnapshot(day=day, **values))
        else:
            for key, value in values.items():
                setattr(snapshot, key, value)
        await self._session.commit()
        return NetWorthPoint(day=day, **{k: _r0(v) for k, v in parts.items()})

    async def net_worth(self, as_of: date) -> NetWorthResponse:
        parts = await self._net_worth_components()
        rows = (
            await self._session.scalars(
                select(NetWorthSnapshot).order_by(NetWorthSnapshot.day)
            )
        ).all()
        history = [
            NetWorthPoint(
                day=r.day,
                net_worth=_r0(r.net_worth),
                liquidity=_r0(r.liquidity),
                investments=_r0(r.investments),
                loans=_r0(r.loans),
                card_debt=_r0(r.card_debt),
            )
            for r in rows
        ]
        warnings = []
        if len(history) < 2:
            warnings.append(
                "O histórico começa a ser gravado a cada sincronização; o gráfico aparece a partir do segundo dia."
            )
        return NetWorthResponse(
            current=NetWorthPoint(day=as_of, **{k: _r0(v) for k, v in parts.items()}),
            history=history,
            warnings=warnings,
        )

    # -- 10. auditoria: o que NÃO entrou na conta de receita/despesa -----------------------------
    async def flow_audit(self, year: int) -> FlowAuditResponse:
        """Mostra o que foi desconsiderado (e por quê), para você conferir se algo real ficou de fora."""
        raw = await self._load_flows(
            _dt(date(year, 1, 1)), _dt(date(year + 1, 1, 1)), net_day_trade=False
        )
        own = get_settings().own_name_list
        warnings: list[str] = []
        groups: list[AuditGroup] = []
        trade = raw[raw["reason"].eq("day_trade")]
        day_trade_net = 0.0
        if not trade.empty:
            signed = trade["amount"].where(
                trade["type"].str.upper().eq("CREDIT"), -trade["amount"]
            )
            day_trade_net = float(signed.sum())
        for reason in (
            "pareada",
            "propria",
            "investimento",
            "emprestimo",
            "cartao",
            "day_trade",
        ):
            rows = raw[raw["reason"].eq(reason)]
            if rows.empty:
                continue
            signed = rows["amount"].where(
                rows["type"].str.upper().eq("CREDIT"), -rows["amount"]
            )
            top = (
                rows.assign(signed=signed)
                .sort_values("amount", ascending=False)
                .head(5)
            )
            groups.append(
                AuditGroup(
                    reason=reason, label=REASON_LABELS[reason], count=len(rows),
                    money_in=_r0(signed[signed > 0].sum()), money_out=_r0(-signed[signed < 0].sum()),
                    examples=[
                        AuditExample(date=r.date.date(), description=str(r.description or ""), account=str(r.account_name or ""), amount=_r0(r.signed))
                        for r in top.itertuples()
                    ],
                )
            )  # fmt: skip
        if not own:
            warnings.append(
                "Defina OWN_NAMES no .env com o seu nome como aparece nos extratos (ex.: OWN_NAMES=Tiago Carneiro Vizoto) "
                "para ignorar PIX e TED que você manda para si mesmo."
            )
        return FlowAuditResponse(
            year=year, counted_income=_r0(raw["income"].sum() + max(day_trade_net, 0.0)),
            counted_expense=_r0(raw["expense"].sum() + max(-day_trade_net, 0.0)),
            groups=groups, day_trade_net=_r0(day_trade_net), own_names=own, warnings=warnings,
        )  # fmt: skip
