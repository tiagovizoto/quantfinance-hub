from datetime import date

from pydantic import BaseModel, Field


class MarketAssumptions(BaseModel):
    """Premissas de mercado (editáveis por query string). Taxas anuais em fração (0.1365 = 13,65%)."""

    cdi_annual: float = Field(0.1365, ge=0, le=1)
    ipca_annual: float = Field(0.0458, ge=-0.2, le=1)
    equity_return_annual: float = Field(0.15, ge=-0.5, le=1)
    equity_vol_annual: float = Field(0.25, ge=0, le=2)
    fixed_income_vol_annual: float = Field(0.02, ge=0, le=1)
    funds_return_annual: float = Field(0.125, ge=-0.5, le=1)
    funds_vol_annual: float = Field(0.06, ge=0, le=1)
    asset_correlation: float = Field(0.3, ge=0, le=1)


# --------------------------------------------------------------------- fluxo de caixa
class MonthlyFlow(BaseModel):
    month: str
    income: float
    expense: float
    savings: float
    savings_rate: float | None


class CashflowResponse(BaseModel):
    as_of: date
    window_months: int
    months_used: list[str]
    monthly: list[MonthlyFlow]
    current_month_to_date: MonthlyFlow
    savings_rate_window: float | None
    burn_rate_monthly: float | None
    burn_rate_daily: float | None
    expense_std_monthly: float | None
    burn_rate_stress_monthly: float | None
    immediate_liquidity: float
    runway_months: float | None
    runway_stress_months: float | None
    warnings: list[str]


class CategoryAnomaly(BaseModel):
    category: str
    current_month_to_date: float
    projected_month: float
    baseline_mean: float
    baseline_std: float | None
    z_score: float | None
    z_basis: str  # "projected" | "month_to_date"
    p_value_upper: float | None
    is_anomaly: bool


class AnomaliesResponse(BaseModel):
    as_of: date
    baseline_months: list[str]
    z_threshold: float
    categories: list[CategoryAnomaly]
    warnings: list[str]


# --------------------------------------------------------------------- resumo anual
class YearMonth(BaseModel):
    month: int  # 1..12
    income: float
    expense: float
    savings: float
    has_data: bool  # False = não há nenhuma transação importada naquele mês


class YearCategory(BaseModel):
    category: str
    total: float
    share: float | None  # fração das despesas do ano
    monthly: list[float]  # 12 valores, jan..dez


class YearSummaryResponse(BaseModel):
    year: int
    available_years: list[int]
    is_partial: bool
    months_with_data: list[int]
    income_total: float
    expense_total: float
    savings_total: float
    savings_rate: float | None
    months: list[YearMonth]
    categories: list[YearCategory]
    warnings: list[str]


# --------------------------------------------------------------------- Monte Carlo
class WealthHorizon(BaseModel):
    months: int
    p10: float
    p50: float
    p90: float
    mean: float
    prob_negative: float


class MonteCarloInputs(BaseModel):
    initial_net_worth: float
    portfolio_value: float
    cash: float
    loans_outstanding: float
    card_debt: float
    savings_mu_monthly: float
    savings_sigma_monthly: float
    savings_months_used: int
    portfolio_return_annual_net: float
    portfolio_vol_annual: float


class FanPoint(BaseModel):
    months: int
    p10: float
    p50: float
    p90: float


class MonteCarloResponse(BaseModel):
    as_of: date
    n_sims: int
    seed: int | None
    inputs: MonteCarloInputs
    horizons: list[WealthHorizon]
    fan: list[FanPoint]  # percentis mês a mês (gráfico em leque)
    warnings: list[str]


# --------------------------------------------------------------------- arbitragem
class ArbitrageRow(BaseModel):
    investment_id: str
    investment_name: str | None
    subtype: str | None
    gross_annual_return: float
    net_annual_return_if_kept: float
    cet_annual: float
    spread_net_vs_cet: float
    capital_considered: float
    value_if_amortize: float
    value_if_keep: float
    vpl_advantage_keep: float
    decision: str  # MANTER_INVESTIDO | AMORTIZAR_DIVIDA
    notes: list[str]


class LoanArbitrage(BaseModel):
    loan_id: str
    loan_name: str | None
    outstanding_balance: float
    cet_annual: float
    horizon_months: int
    suggested_source_to_liquidate: str | None
    rows: list[ArbitrageRow]


class ArbitrageResponse(BaseModel):
    as_of: date
    discount_rate_annual: float
    loans: list[LoanArbitrage]
    warnings: list[str]


# --------------------------------------------------------------------- plano de quitação
class LoanPlan(BaseModel):
    loan_id: str
    name: str
    balance: float
    cet_annual: float | None
    amortization: str  # PRICE | SAC
    remaining_installments: int
    installment_first: float  # estimada
    installment_last: float  # quanto é liberado quando termina
    interest_remaining: float  # juros que ainda serão pagos no ritmo atual
    ends_in_months: int | None
    ends_on: str | None  # AAAA-MM


class PayoffStep(BaseModel):
    loan_id: str
    name: str
    month: int
    on: str


class StrategyResult(BaseModel):
    key: str  # baseline | avalanche | snowball
    label: str
    months_to_debt_free: int | None
    debt_free_on: str | None
    total_interest: float
    interest_saved: float
    months_saved: int
    payoff_order: list[PayoffStep]


class DebtPoint(BaseModel):
    month: int
    on: str
    baseline: float
    avalanche: float
    snowball: float


class CostPoint(BaseModel):
    """Do que é feito o pagamento de cada mês: juros (dinheiro perdido) x principal (dívida que cai)."""

    month: int
    on: str
    baseline_interest: float
    baseline_principal: float
    avalanche_interest: float
    avalanche_principal: float
    snowball_interest: float
    snowball_principal: float


class TimelinePoint(BaseModel):
    month: int
    on: str
    total: float
    by_loan: list[float]  # mesma ordem de `loans`
    commitment: float | None  # fração da renda média


class LoanStrategyResponse(BaseModel):
    as_of: date
    extra_monthly: float
    avg_monthly_income: float | None
    total_balance: float
    total_installments_now: float
    loans: list[LoanPlan]
    strategies: list[StrategyResult]
    best_strategy: str | None
    debt_curve: list[DebtPoint]
    cost_curve: list[CostPoint] = []
    timeline: list[TimelinePoint]
    warnings: list[str]


# --------------------------------------------------------------------- explorador de gastos
class SpendMerchant(BaseModel):
    name: str
    total: float
    count: int
    share: float | None  # fração do total da categoria (ou do período, na lista geral)


class SpendTransaction(BaseModel):
    id: str
    date: date
    description: str
    category: str
    account: str
    amount: float


class SpendCategory(BaseModel):
    category: str
    total: float
    share: float | None  # fração das despesas do período
    count: int
    average: float  # valor médio por lançamento
    monthly: list[float]  # 12 valores (jan..dez) do ANO inteiro, para ver a tendência
    merchants: list[SpendMerchant]
    top_transactions: list[SpendTransaction]


class SpendDay(BaseModel):
    date: date
    expense: float
    count: int


class SpendWeekday(BaseModel):
    weekday: int  # 0 = segunda
    label: str
    total: float
    count: int
    average_per_day: float  # total / nº de dias dessa semana no intervalo com dados


class RecurringExpense(BaseModel):
    name: str
    category: str
    monthly_amount: float
    yearly_cost: float
    months_seen: int
    last_seen: date


class SpendingResponse(BaseModel):
    year: int
    month: int | None
    available_years: list[int]
    period_start: date
    period_end: date  # último dia (inclusive)
    income_total: float
    expense_total: float
    savings_total: float
    transaction_count: int
    categories: list[SpendCategory]
    top_merchants: list[SpendMerchant]
    top_transactions: list[SpendTransaction]
    days: list[SpendDay]
    weekdays: list[SpendWeekday]
    recurring: list[RecurringExpense]
    recurring_monthly_total: float
    warnings: list[str]


# --------------------------------------------------------------------- risco de crédito
class CardUtilization(BaseModel):
    account_id: str
    name: str
    credit_limit: float
    used: float
    utilization: float | None


class CreditRiskResponse(BaseModel):
    as_of: date
    credit_limit_total: float
    credit_used_total: float
    utilization_total: float | None
    cards: list[CardUtilization]
    immediate_liquidity: float
    loans_outstanding: float
    total_debt: float
    debt_coverage_ratio: float | None
    monthly_installments_estimated: float
    avg_monthly_income: float | None
    income_commitment: float | None
    flags: list[str]
    warnings: list[str]
