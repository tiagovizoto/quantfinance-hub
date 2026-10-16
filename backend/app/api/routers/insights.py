from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.quant import AsOf, Assumptions
from app.database import get_session
from app.schemas.insights import (
    CashForecastResponse,
    CategoryChangeResponse,
    ConcentrationResponse,
    InstallmentsResponse,
    MoneyGraphResponse,
    NetWorthResponse,
    PaceResponse,
    PortfolioResponse,
    StructureResponse,
)
from app.services.insights_engine import InsightsEngine

router = APIRouter(prefix="/quant", tags=["insights"])


def get_engine(session: Annotated[AsyncSession, Depends(get_session)]) -> InsightsEngine:
    return InsightsEngine(session)


Engine = Annotated[InsightsEngine, Depends(get_engine)]
Year = Annotated[int | None, Query(ge=2000, le=2100, description="Ano civil (padrão: atual)")]
Month = Annotated[int | None, Query(ge=1, le=12, description="Mês (vazio = ano todo)")]


@router.get("/cash-forecast", response_model=CashForecastResponse)
async def cash_forecast(
    engine: Engine, as_of: AsOf = None, days: Annotated[int, Query(ge=7, le=120)] = 60
) -> CashForecastResponse:
    """Saldo diário previsto com fixos, salário, parcelas, fatura e gasto variável."""
    return await engine.cash_forecast(as_of or date.today(), days)


@router.get("/pace", response_model=PaceResponse)
async def pace(engine: Engine, as_of: AsOf = None) -> PaceResponse:
    """Gasto acumulado do mês contra o mês passado e a média."""
    return await engine.pace(as_of or date.today())


@router.get("/category-change", response_model=CategoryChangeResponse)
async def category_change(
    engine: Engine,
    year: Year = None,
    month: Annotated[int | None, Query(ge=1, le=12)] = None,
    mode: Literal["previous_month", "previous_year", "average_6m"] = "previous_month",
) -> CategoryChangeResponse:
    today = date.today()
    return await engine.category_change(year or today.year, month or today.month, mode)


@router.get("/structure", response_model=StructureResponse)
async def structure(engine: Engine, year: Year = None) -> StructureResponse:
    return await engine.structure(year or date.today().year)


@router.get("/concentration", response_model=ConcentrationResponse)
async def concentration(engine: Engine, year: Year = None, month: Month = None) -> ConcentrationResponse:
    return await engine.concentration(year or date.today().year, month)


@router.get("/portfolio", response_model=PortfolioResponse)
async def portfolio(engine: Engine, assumptions: Assumptions, as_of: AsOf = None) -> PortfolioResponse:
    return await engine.portfolio(as_of or date.today(), assumptions)


@router.get("/installments", response_model=InstallmentsResponse)
async def installments(engine: Engine, as_of: AsOf = None) -> InstallmentsResponse:
    return await engine.installments(as_of or date.today())


@router.get("/money-graph", response_model=MoneyGraphResponse)
async def money_graph(engine: Engine, year: Year = None, month: Month = None) -> MoneyGraphResponse:
    return await engine.money_graph(year or date.today().year, month)


@router.get("/net-worth", response_model=NetWorthResponse)
async def net_worth(engine: Engine, as_of: AsOf = None) -> NetWorthResponse:
    return await engine.net_worth(as_of or date.today())
