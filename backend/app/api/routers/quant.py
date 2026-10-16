from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.quant import (
    AnomaliesResponse,
    ArbitrageResponse,
    CashflowResponse,
    CreditRiskResponse,
    LoanStrategyResponse,
    MarketAssumptions,
    MonteCarloResponse,
    SpendingResponse,
    YearSummaryResponse,
)
from app.services.quant_engine import InsufficientDataError, QuantEngine

router = APIRouter(prefix="/quant", tags=["quant"])

AsOf = Annotated[date | None, Query(description="Data de referência (padrão: hoje)")]
_DEFAULTS = MarketAssumptions()


def get_assumptions(
    cdi_annual: float = _DEFAULTS.cdi_annual,
    ipca_annual: float = _DEFAULTS.ipca_annual,
    equity_return_annual: float = _DEFAULTS.equity_return_annual,
    equity_vol_annual: float = _DEFAULTS.equity_vol_annual,
    fixed_income_vol_annual: float = _DEFAULTS.fixed_income_vol_annual,
    funds_return_annual: float = _DEFAULTS.funds_return_annual,
    funds_vol_annual: float = _DEFAULTS.funds_vol_annual,
    asset_correlation: float = _DEFAULTS.asset_correlation,
) -> MarketAssumptions:
    """Premissas de mercado por query string (taxas em fração: 0.1365 = 13,65%)."""
    try:
        return MarketAssumptions(
            cdi_annual=cdi_annual,
            ipca_annual=ipca_annual,
            equity_return_annual=equity_return_annual,
            equity_vol_annual=equity_vol_annual,
            fixed_income_vol_annual=fixed_income_vol_annual,
            funds_return_annual=funds_return_annual,
            funds_vol_annual=funds_vol_annual,
            asset_correlation=asset_correlation,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc


Assumptions = Annotated[MarketAssumptions, Depends(get_assumptions)]


def get_engine(session: Annotated[AsyncSession, Depends(get_session)]) -> QuantEngine:
    return QuantEngine(session)


Engine = Annotated[QuantEngine, Depends(get_engine)]


@router.get("/year-summary", response_model=YearSummaryResponse)
async def year_summary(
    engine: Engine, year: Annotated[int | None, Query(ge=2000, le=2100, description="Ano civil (padrão: atual)")] = None
) -> YearSummaryResponse:
    """Para onde foi o dinheiro no ano: totais, meses e categorias x mês."""
    today = date.today()
    return await engine.year_summary(year or today.year, today)


@router.get("/spending", response_model=SpendingResponse)
async def spending(
    engine: Engine,
    year: Annotated[int | None, Query(ge=2000, le=2100, description="Ano civil (padrão: atual)")] = None,
    month: Annotated[int | None, Query(ge=1, le=12, description="Mês (vazio = ano todo)")] = None,
) -> SpendingResponse:
    """Explorador: categorias com comerciantes, dias, dias da semana, recorrentes e maiores gastos."""
    today = date.today()
    return await engine.spending(year or today.year, month, today)


@router.get("/cashflow", response_model=CashflowResponse)
async def cashflow(
    engine: Engine,
    as_of: AsOf = None,
    window: Annotated[int, Query(ge=2, le=12)] = 6,
    include_investments: bool = False,
) -> CashflowResponse:
    """Poupança, burn rate e runway (normal e estresse +2σ)."""
    return await engine.cashflow(as_of or date.today(), window=window, include_investments=include_investments)


@router.get("/anomalies", response_model=AnomaliesResponse)
async def anomalies(
    engine: Engine,
    as_of: AsOf = None,
    window: Annotated[int, Query(ge=3, le=12)] = 6,
    threshold: Annotated[float, Query(gt=0, le=6)] = 2.0,
) -> AnomaliesResponse:
    """Z-score do gasto do mês por categoria vs. média dos últimos meses."""
    return await engine.anomalies(as_of or date.today(), window=window, threshold=threshold)


@router.get("/monte-carlo", response_model=MonteCarloResponse)
async def monte_carlo(
    engine: Engine,
    assumptions: Assumptions,
    as_of: AsOf = None,
    n_sims: Annotated[int, Query(ge=1_000, le=100_000)] = 10_000,
    seed: int | None = None,
) -> MonteCarloResponse:
    """Patrimônio líquido em 12/24/60 meses (P10/P50/P90)."""
    try:
        return await engine.monte_carlo(as_of or date.today(), assumptions, n_sims=n_sims, seed=seed)
    except InsufficientDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/arbitrage", response_model=ArbitrageResponse)
async def arbitrage(engine: Engine, assumptions: Assumptions, as_of: AsOf = None) -> ArbitrageResponse:
    """Amortizar dívida vs. manter investido (VPL, IR regressivo)."""
    return await engine.arbitrage(as_of or date.today(), assumptions)


@router.get("/loan-strategy", response_model=LoanStrategyResponse)
async def loan_strategy(
    engine: Engine,
    as_of: AsOf = None,
    extra: Annotated[float, Query(ge=0, le=1_000_000, description="Valor extra por mês, em reais")] = 0.0,
) -> LoanStrategyResponse:
    """Plano de quitação: parcelas atuais vs. maior CET primeiro vs. menor saldo primeiro."""
    return await engine.loan_strategy(as_of or date.today(), extra)


@router.get("/credit-risk", response_model=CreditRiskResponse)
async def credit_risk(
    engine: Engine, as_of: AsOf = None, include_investments: bool = False
) -> CreditRiskResponse:
    """Utilização de limite, cobertura da dívida e comprometimento de renda."""
    return await engine.credit_risk(as_of or date.today(), include_investments=include_investments)
