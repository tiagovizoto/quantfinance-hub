"""Schemas das análises adicionais: caixa, ritmo, variação, estrutura, concentração, carteira, parcelados, rede."""

from datetime import date
from typing import Literal

from pydantic import BaseModel


# --------------------------------------------------------------------- projeção de caixa
class CashEvent(BaseModel):
    date: date
    label: str
    amount: float  # negativo = saída
    kind: Literal["recurring", "income", "loan", "card"]


class CashPoint(BaseModel):
    date: date
    base: float
    low: float  # cenário ruim (p10 do gasto variável)
    high: float  # cenário bom (p90)


class CashForecastResponse(BaseModel):
    as_of: date
    days: int
    starting_balance: float
    ending_balance: float
    min_balance: float
    min_date: date
    first_negative_base: date | None
    first_negative_low: date | None
    daily_variable_spend: float
    points: list[CashPoint]
    events: list[CashEvent]
    warnings: list[str]


# --------------------------------------------------------------------- ritmo do mês
class PaceResponse(BaseModel):
    as_of: date
    month_label: str  # "2026-10"
    day_of_month: int
    days: list[int]  # 1..31
    current: list[float | None]  # acumulado até hoje; depois do dia atual = None
    previous: list[float]  # mês passado, acumulado
    average: list[float]  # média dos meses fechados, acumulado
    previous_label: str | None
    months_in_average: int
    spent_to_date: float
    previous_to_date: float | None
    average_to_date: float | None
    projected_end: float | None  # no ritmo atual, vs. o ritmo típico
    typical_end: float | None
    previous_total: float | None
    warnings: list[str]


# --------------------------------------------------------------------- variação entre períodos
class CategoryChange(BaseModel):
    category: str
    current: float
    base: float
    delta: float
    delta_pct: float | None


class CategoryChangeResponse(BaseModel):
    year: int
    month: int
    mode: Literal["previous_month", "previous_year", "average_6m"]
    current_label: str
    base_label: str
    current_total: float
    base_total: float
    rows: list[CategoryChange]
    warnings: list[str]


# --------------------------------------------------------------------- fixo x variável x avulso
class StructureMonth(BaseModel):
    month: int
    fixed: float
    variable: float
    one_off: float
    total: float


class StructureResponse(BaseModel):
    year: int
    months: list[StructureMonth]
    fixed_total: float
    variable_total: float
    one_off_total: float
    fixed_share: float | None
    one_off_threshold: float
    fixed_items: list[str]
    warnings: list[str]


# --------------------------------------------------------------------- concentração (Pareto)
class ParetoItem(BaseModel):
    name: str
    total: float
    share: float
    cumulative: float


class ParetoSeries(BaseModel):
    items: list[ParetoItem]
    count: int  # quantos itens existem no total
    n_for_50: int
    n_for_80: int


class ConcentrationResponse(BaseModel):
    year: int
    month: int | None
    expense_total: float
    categories: ParetoSeries
    merchants: ParetoSeries
    warnings: list[str]


# --------------------------------------------------------------------- carteira de investimentos
class PortfolioClass(BaseModel):
    asset_class: str
    label: str
    value: float
    share: float


class PortfolioPosition(BaseModel):
    id: str
    name: str
    subtype: str | None
    asset_class: str
    value: float
    share: float
    indexer: str | None
    rate_pct: float | None  # % a.a. usado na comparação com o CDI
    rate_source: Literal["realizada_12m", "contratada", "estimada"]
    spread_vs_cdi: float | None  # pontos percentuais (rate - CDI)
    profit: float | None
    profit_pct: float | None
    due_date: date | None
    days_to_due: int | None


class MaturityBucket(BaseModel):
    month: str  # AAAA-MM
    value: float
    items: list[str]


class PortfolioResponse(BaseModel):
    as_of: date
    total: float
    cdi_annual_pct: float
    by_class: list[PortfolioClass]
    positions: list[PortfolioPosition]
    maturities: list[MaturityBucket]
    matures_12m: float
    warnings: list[str]


# --------------------------------------------------------------------- parcelados no cartão
class InstallmentPurchase(BaseModel):
    description: str
    account: str
    amount: float
    number: int
    total: int
    remaining: int  # parcelas que ainda vão cair
    remaining_total: float
    ends_on: str  # AAAA-MM


class InstallmentMonth(BaseModel):
    month: str
    total: float
    count: int


class InstallmentsResponse(BaseModel):
    as_of: date
    months: list[InstallmentMonth]
    purchases: list[InstallmentPurchase]
    total_committed: float
    next_month: float
    warnings: list[str]


# --------------------------------------------------------------------- rede do dinheiro (grafo)
class GraphNode(BaseModel):
    id: str
    label: str
    kind: Literal["income", "account", "category", "merchant"]
    value: float


class GraphLink(BaseModel):
    source: str
    target: str
    value: float


class MoneyGraphResponse(BaseModel):
    year: int
    month: int | None
    nodes: list[GraphNode]
    links: list[GraphLink]
    expense_total: float
    income_total: float
    warnings: list[str]


# --------------------------------------------------------------------- patrimônio ao longo do tempo
class NetWorthPoint(BaseModel):
    day: date
    net_worth: float
    liquidity: float
    investments: float
    loans: float
    card_debt: float


class NetWorthResponse(BaseModel):
    current: NetWorthPoint
    history: list[NetWorthPoint]
    warnings: list[str]
