"""Integração com a API da Pluggy (Open Finance) e upsert no banco.

Seções:
1. Exceções
2. Helpers de conversão (Decimal / datas)
3. Cliente HTTP assíncrono (auth, retry, paginação)
4. Mappers payload Pluggy -> colunas do ORM
5. Upsert idempotente (SQLite e PostgreSQL)
6. Serviço de sincronização (``PluggySyncService``)
7. Entrada de linha de comando
"""

import argparse
import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Final

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.models import Account, Bill, Investment, Item, Loan, Transaction

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

ZERO: Final = Decimal("0.00")
# SQLite >= 3.32 aceita 32766 parâmetros; PostgreSQL 65535. Ficamos abaixo dos dois.
_MAX_BIND_PARAMS: Final = 30_000
_TRANSACTIONS_PAGE_SIZE: Final = 500
# Conectores nem sempre suportam todos os produtos: esses status viram aviso, não erro.
_SOFT_FAIL_STATUSES: Final = frozenset({400, 403, 404, 422})


# ---------------------------------------------------------------------------
# 1. Exceções
# ---------------------------------------------------------------------------
class PluggyError(Exception):
    """Erro base da integração."""


class PluggyAuthError(PluggyError):
    """Falha ao autenticar (CLIENT_ID/CLIENT_SECRET inválidos ou ausentes)."""


class PluggyAPIError(PluggyError):
    def __init__(self, status_code: int, message: str, path: str) -> None:
        super().__init__(f"Pluggy {path} -> HTTP {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.path = path


# ---------------------------------------------------------------------------
# 2. Helpers de conversão
# ---------------------------------------------------------------------------
def to_decimal(value: Any, places: int = 2) -> Decimal | None:
    """Converte para Decimal sem passar por float binário; None se inválido."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    try:
        return number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN)
    except InvalidOperation:
        return None


def to_datetime(value: Any) -> datetime | None:
    """ISO-8601 -> datetime UTC *naive* (padrão de armazenamento do projeto)."""
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def to_date(value: Any) -> date | None:
    parsed = to_datetime(value)
    return parsed.date() if parsed else None


def to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_str(value: Any, max_len: int) -> str | None:
    """Texto truncado ao tamanho da coluna (PostgreSQL rejeita overflow)."""
    if value is None:
        return None
    return str(value)[:max_len]


def first_not_none(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


# ---------------------------------------------------------------------------
# 3. Cliente HTTP
# ---------------------------------------------------------------------------
class PluggyClient:
    """Cliente assíncrono da Pluggy.

    - ``POST /auth`` com clientId/clientSecret gera a ``X-API-KEY`` (validade de 2 h;
      renovamos aos 110 min ou ao receber 401).
    - Retry com backoff exponencial em 429, 5xx e erros de transporte.
    - Nunca loga a API key nem o client secret.
    """

    _API_KEY_TTL_SECONDS: Final = 110 * 60

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._http = httpx.AsyncClient(
            base_url=self._settings.pluggy_base_url,
            timeout=self._settings.pluggy_timeout_seconds,
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._api_key: str | None = None
        self._api_key_expires_at: float = 0.0
        self._auth_lock = asyncio.Lock()

    async def __aenter__(self) -> "PluggyClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- autenticação -------------------------------------------------------
    async def authenticate(self, *, force: bool = False) -> None:
        async with self._auth_lock:
            if not force and self._api_key and time.monotonic() < self._api_key_expires_at:
                return
            client_id = self._settings.pluggy_client_id
            client_secret = self._settings.pluggy_client_secret.get_secret_value()
            if not client_id or not client_secret:
                raise PluggyAuthError("PLUGGY_CLIENT_ID / PLUGGY_CLIENT_SECRET não configurados.")
            try:
                response = await self._http.post(
                    "/auth", json={"clientId": client_id, "clientSecret": client_secret}
                )
            except httpx.HTTPError as exc:
                raise PluggyAuthError(f"Falha de rede ao autenticar na Pluggy: {exc!r}") from exc
            if response.status_code != 200:
                raise PluggyAuthError(
                    f"Autenticação recusada pela Pluggy (HTTP {response.status_code})."
                )
            api_key = response.json().get("apiKey")
            if not api_key:
                raise PluggyAuthError("Resposta de /auth sem apiKey.")
            self._api_key = api_key
            self._api_key_expires_at = time.monotonic() + self._API_KEY_TTL_SECONDS

    # -- requisições --------------------------------------------------------
    async def request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        await self.authenticate()
        max_retries = self._settings.pluggy_max_retries
        reauthenticated = False
        last_error = "sem resposta"
        last_status = 0

        for attempt in range(max_retries + 1):
            try:
                response = await self._http.request(
                    method, path, params=params, headers={"X-API-KEY": self._api_key or ""}
                )
            except httpx.TransportError as exc:
                last_error, last_status = repr(exc), 0
                await self._sleep_before_retry(attempt, None)
                continue

            status = response.status_code
            if status == 401 and not reauthenticated:
                reauthenticated = True
                await self.authenticate(force=True)
                continue
            if status == 429 or status >= 500:
                last_error, last_status = self._error_message(response), status
                await self._sleep_before_retry(attempt, response.headers.get("Retry-After"))
                continue
            if status >= 400:
                raise PluggyAPIError(status, self._error_message(response), path)
            return response.json()

        raise PluggyAPIError(last_status, f"esgotadas as tentativas ({last_error})", path)

    async def iter_pages(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        page_size: int | None = None,
    ) -> AsyncIterator[list[JsonDict]]:
        """Itera página a página (``results`` + ``totalPages``)."""
        query: JsonDict = dict(params or {})
        if page_size:
            query["pageSize"] = page_size
        page = 1
        while True:
            if page > 1:
                query["page"] = page
            payload = await self.request("GET", path, params=query)
            if isinstance(payload, list):
                yield payload
                return
            results: list[JsonDict] = payload.get("results") or []
            yield results
            if not results or page >= int(payload.get("totalPages") or 1):
                return
            page += 1

    async def fetch_all(
        self, path: str, params: Mapping[str, Any] | None = None, *, page_size: int | None = None
    ) -> list[JsonDict]:
        items: list[JsonDict] = []
        async for page in self.iter_pages(path, params, page_size=page_size):
            items.extend(page)
        return items

    # -- internos -----------------------------------------------------------
    async def _sleep_before_retry(self, attempt: int, retry_after: str | None) -> None:
        if attempt >= self._settings.pluggy_max_retries:
            return
        delay = min(30.0, 2.0**attempt + random.random())
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 60.0))
            except ValueError:
                pass
        await asyncio.sleep(delay)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return response.reason_phrase or "erro"
        if isinstance(body, dict):
            return str(body.get("message") or body.get("error") or response.reason_phrase)
        return response.reason_phrase or "erro"


# ---------------------------------------------------------------------------
# 4. Mappers: payload Pluggy -> dict de colunas
#    (todas as chaves sempre presentes: exigência do INSERT multi-linha)
# ---------------------------------------------------------------------------
def map_item(raw: Mapping[str, Any]) -> JsonDict:
    connector = raw.get("connector") or {}
    return {
        "id": str(raw["id"]),
        "connector_id": to_int(connector.get("id")),
        "connector_name": to_str(connector.get("name"), 120),
        "status": to_str(raw.get("status"), 32),
        "execution_status": to_str(raw.get("executionStatus"), 32),
        "pluggy_created_at": to_datetime(raw.get("createdAt")),
        "pluggy_updated_at": to_datetime(raw.get("updatedAt")),
        "pluggy_last_updated_at": to_datetime(raw.get("lastUpdatedAt")),
    }


def map_account(raw: Mapping[str, Any], item_id: str) -> JsonDict:
    bank = raw.get("bankData") or {}
    credit = raw.get("creditData") or {}
    return {
        "id": str(raw["id"]),
        "item_id": item_id,
        "type": to_str(raw.get("type"), 16) or "BANK",
        "subtype": to_str(raw.get("subtype"), 32),
        "name": to_str(raw.get("name"), 255) or "Conta",
        "marketing_name": to_str(raw.get("marketingName"), 255),
        "number": to_str(raw.get("number"), 64),
        "currency_code": to_str(raw.get("currencyCode"), 3) or "BRL",
        "balance": to_decimal(raw.get("balance")) or ZERO,
        # `overdraftLimit` (nome citado no escopo) ou bankData.overdraftContractedLimit (Pluggy).
        "overdraft_limit": first_not_none(
            to_decimal(bank.get("overdraftContractedLimit")), to_decimal(raw.get("overdraftLimit"))
        ),
        "overdraft_used": to_decimal(bank.get("overdraftUsedLimit")),
        "credit_level": to_str(credit.get("level"), 64),
        "credit_brand": to_str(credit.get("brand"), 64),
        "credit_balance_close_date": to_date(credit.get("balanceCloseDate")),
        "credit_balance_due_date": to_date(credit.get("balanceDueDate")),
        "credit_limit": to_decimal(credit.get("creditLimit")),
        "credit_available_limit": to_decimal(credit.get("availableCreditLimit")),
        "credit_minimum_payment": to_decimal(credit.get("minimumPayment")),
        "is_active": True,
        "last_synced_at": datetime.now(UTC).replace(tzinfo=None),
    }


def map_bill(raw: Mapping[str, Any], account_id: str) -> JsonDict:
    charges: list[JsonDict] = raw.get("financeCharges") or []
    charges_total = sum(
        (to_decimal(charge.get("amount")) or ZERO for charge in charges), start=ZERO
    )
    return {
        "id": str(raw["id"]),
        "account_id": account_id,
        "due_date": to_date(raw.get("dueDate")),
        "total_amount": to_decimal(raw.get("totalAmount")) or ZERO,
        "total_amount_currency_code": to_str(raw.get("totalAmountCurrencyCode"), 3) or "BRL",
        "minimum_payment_amount": to_decimal(raw.get("minimumPaymentAmount")),
        "allows_installments": raw.get("allowsInstallments"),
        "finance_charges": charges or None,
        "finance_charges_total": charges_total,
    }


def map_transaction(
    raw: Mapping[str, Any], account_id: str, known_bill_ids: set[str]
) -> JsonDict | None:
    """Retorna None para registros sem id/data/valor (não dá para persistir)."""
    tx_id = raw.get("id")
    date_value = to_datetime(raw.get("date"))
    amount = to_decimal(raw.get("amount"))
    if not tx_id or date_value is None or amount is None:
        logger.warning("Transação ignorada (id/data/valor ausentes): %s", tx_id)
        return None

    card_meta = raw.get("creditCardMetadata") or {}
    bill_id = card_meta.get("billId")
    return {
        "id": str(tx_id),
        "account_id": account_id,
        "bill_id": bill_id if bill_id in known_bill_ids else None,
        "date": date_value,
        "description": to_str(raw.get("description"), 512),
        "description_raw": to_str(raw.get("descriptionRaw"), 512),
        "amount": amount,
        "amount_in_account_currency": to_decimal(raw.get("amountInAccountCurrency")),
        "currency_code": to_str(raw.get("currencyCode"), 3) or "BRL",
        "type": (to_str(raw.get("type"), 8) or ("DEBIT" if amount < 0 else "CREDIT")).upper(),
        "status": to_str(raw.get("status"), 16),
        "operation_type": to_str(raw.get("operationType"), 64),
        "category": to_str(raw.get("category"), 120),
        "category_id": to_str(raw.get("categoryId"), 32),
        "provider_code": to_str(raw.get("providerCode"), 64),
        "balance": to_decimal(raw.get("balance")),
        "installment_number": to_int(card_meta.get("installmentNumber")),
        "total_installments": to_int(card_meta.get("totalInstallments")),
        "payment_data": raw.get("paymentData"),
        "credit_card_metadata": card_meta or None,
        "merchant": raw.get("merchant"),
    }


_INDEXER_ALIASES: Final = {
    "PRE": "PRE",
    "PRE_FIXED": "PRE",
    "PREFIXED": "PRE",
    "PREFIXADO": "PRE",
    "FIXED": "PRE",
}


def classify_asset(type_: str | None) -> str:
    kind = (type_ or "").upper()
    if kind == "FIXED_INCOME":
        return "RENDA_FIXA"
    if kind in {"EQUITY", "ETF"}:
        return "RENDA_VARIAVEL"
    if kind == "MUTUAL_FUND":
        return "FUNDOS"
    return "OUTROS"


def map_investment(raw: Mapping[str, Any], item_id: str) -> JsonDict:
    gross = first_not_none(to_decimal(raw.get("balance")), to_decimal(raw.get("amount")))
    taxes = (to_decimal(raw.get("taxes")) or ZERO) + (to_decimal(raw.get("taxes2")) or ZERO)
    net = gross - taxes if gross is not None else None
    invested = to_decimal(raw.get("amountOriginal"))
    profit = to_decimal(raw.get("amountProfit"))
    if profit is None and gross is not None and invested is not None:
        profit = gross - invested

    rate_type = to_str(raw.get("rateType"), 16)
    indexer = _INDEXER_ALIASES.get(rate_type.upper(), rate_type.upper()) if rate_type else None
    institution = raw.get("institution") or {}
    inv_type = to_str(raw.get("type"), 32)

    return {
        "id": str(raw["id"]),
        "item_id": item_id,
        "type": inv_type,
        "subtype": to_str(raw.get("subtype"), 48),
        "asset_class": classify_asset(inv_type),
        "name": to_str(raw.get("name"), 255),
        "code": to_str(raw.get("code"), 64),
        "isin": to_str(raw.get("isin"), 16),
        "issuer": to_str(raw.get("issuer"), 255),
        "institution_name": to_str(institution.get("name"), 255),
        "currency_code": to_str(raw.get("currencyCode"), 3) or "BRL",
        "status": to_str(raw.get("status"), 32),
        "amount_invested": invested,
        "gross_balance": gross,
        "net_balance": net,
        "taxes": taxes,
        "profit": profit,
        "quantity": to_decimal(raw.get("quantity"), 8),
        "unit_value": to_decimal(raw.get("value"), 8),
        "indexer": indexer,
        "rate": to_decimal(raw.get("rate"), 6),
        "fixed_annual_rate": to_decimal(raw.get("fixedAnnualRate"), 6),
        "annual_rate": to_decimal(raw.get("annualRate"), 6),
        "last_month_rate": to_decimal(raw.get("lastMonthRate"), 6),
        "last_twelve_months_rate": to_decimal(raw.get("lastTwelveMonthsRate"), 6),
        "issue_date": to_date(raw.get("issueDate")),
        "due_date": to_date(raw.get("dueDate")),
        "position_date": to_date(raw.get("date")),
        "raw_metadata": raw.get("metadata"),
        "is_active": True,
    }


def monthly_from_annual_pct(annual_pct: Decimal | None) -> Decimal | None:
    """Taxa % a.a. -> % a.m. equivalente (juros compostos)."""
    if annual_pct is None:
        return None
    base = Decimal(1) + annual_pct / Decimal(100)
    if base <= 0:
        return None
    monthly = (base ** (Decimal(1) / Decimal(12)) - Decimal(1)) * Decimal(100)
    return monthly.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)


def map_loan(raw: Mapping[str, Any], item_id: str) -> JsonDict:
    installments = raw.get("installments") or {}
    payments = raw.get("payments") or {}
    total = to_int(installments.get("totalNumberOfInstallments"))
    paid = to_int(installments.get("paidInstallments"))
    remaining = to_int(installments.get("contractRemainingNumber"))
    if remaining is None and total is not None and paid is not None:
        remaining = max(total - paid, 0)

    # A Pluggy não padroniza o saldo devedor entre conectores: tentamos os nomes conhecidos.
    outstanding = first_not_none(
        to_decimal(raw.get("outstandingBalance")),
        to_decimal(raw.get("contractOutstandingBalance")),
        to_decimal(payments.get("contractOutstandingBalance")),
    )
    cet_annual = to_decimal(first_not_none(raw.get("CET"), raw.get("cet")), 6)

    return {
        "id": str(raw["id"]),
        "item_id": item_id,
        "ipoc_code": to_str(raw.get("ipocCode"), 128),
        "product_name": to_str(raw.get("productName"), 255),
        "type": to_str(raw.get("type"), 64),
        "contract_number": to_str(raw.get("contractNumber"), 128),
        "currency_code": to_str(raw.get("currencyCode"), 3) or "BRL",
        "contract_date": to_date(raw.get("contractDate") or raw.get("date")),
        "settlement_date": to_date(raw.get("settlementDate")),
        "due_date": to_date(raw.get("dueDate")),
        "first_installment_due_date": to_date(raw.get("firstInstallmentDueDate")),
        "disbursement_dates": raw.get("disbursementDates"),
        "contract_amount": first_not_none(
            to_decimal(raw.get("contractAmount")), to_decimal(raw.get("contractedAmount"))
        ),
        "outstanding_balance": outstanding,
        "cet_annual": cet_annual,
        "cet_monthly": monthly_from_annual_pct(cet_annual),
        "amortization_scheduled": to_str(raw.get("amortizationScheduled"), 32),
        "installment_periodicity": to_str(raw.get("installmentPeriodicity"), 32),
        "total_installments": total,
        "paid_installments": paid,
        "remaining_installments": remaining,
        "due_installments": to_int(installments.get("dueInstallments")),
        "past_due_installments": to_int(installments.get("pastDueInstallments")),
        "interest_rates": raw.get("interestRates"),
        "contracted_fees": raw.get("contractedFees"),
        "contracted_finance_charges": raw.get("contractedFinanceCharges"),
        "warranties": raw.get("warranties"),
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# 5. Upsert idempotente
# ---------------------------------------------------------------------------
async def upsert_rows(
    session: AsyncSession, model: type[Any], rows: Sequence[JsonDict], *, pk: str = "id"
) -> int:
    """INSERT ... ON CONFLICT (pk) DO UPDATE, em lotes. Funciona em SQLite e PostgreSQL.

    Preserva ``created_at`` e atualiza ``updated_at``. Retorna o nº de linhas únicas.
    """
    unique_rows = list({row[pk]: row for row in rows}.values())  # PG não aceita pk repetida no lote
    if not unique_rows:
        return 0

    dialect = session.bind.dialect.name  # type: ignore[union-attr]
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        raise NotImplementedError(f"Upsert não implementado para o dialeto '{dialect}'.")

    columns = list(unique_rows[0].keys())
    chunk_size = max(1, _MAX_BIND_PARAMS // len(columns))
    for start in range(0, len(unique_rows), chunk_size):
        statement = dialect_insert(model).values(unique_rows[start : start + chunk_size])
        update_set: JsonDict = {col: statement.excluded[col] for col in columns if col != pk}
        update_set["updated_at"] = func.now()
        await session.execute(
            statement.on_conflict_do_update(index_elements=[pk], set_=update_set)
        )
    return len(unique_rows)


async def deactivate_missing(
    session: AsyncSession, model: type[Any], item_id: str, seen_ids: Sequence[str]
) -> int:
    """Marca como inativos os registros do item que a Pluggy não devolveu mais."""
    statement = (
        update(model)
        .where(model.item_id == item_id, model.is_active.is_(True))
        .values(is_active=False)
    )
    if seen_ids:
        statement = statement.where(model.id.not_in(list(seen_ids)))
    result = await session.execute(statement)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 6. Serviço de sincronização
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class SyncResult:
    item_id: str
    accounts: int = 0
    transactions: int = 0
    bills: int = 0
    investments: int = 0
    loans: int = 0
    warnings: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    def as_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class _AccountRef:
    id: str
    type: str


class PluggySyncService:
    """Orquestra a carga completa de um ``itemId`` (idempotente; pode rodar quantas vezes quiser).

    Cada etapa faz commit ao terminar: se uma etapa posterior falhar, o que já foi
    gravado permanece válido e a próxima execução reaproveita via upsert.
    """

    def __init__(
        self,
        client: PluggyClient,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings | None = None,
    ) -> None:
        self._client = client
        self._session_factory = session_factory
        self._settings = settings or get_settings()

    async def sync_item(self, item_id: str, *, full_history: bool = False) -> SyncResult:
        result = SyncResult(item_id=item_id)
        async with self._session_factory() as session:
            try:
                await self._sync_item_row(session, item_id, result)
                accounts = await self._sync_accounts(session, item_id, result)
                await self._sync_bills(session, accounts, result)
                await self._sync_transactions(session, accounts, result, full_history)
                await self._sync_investments(session, item_id, result)
                await self._sync_loans(session, item_id, result)
                await session.execute(
                    update(Item).where(Item.id == item_id).values(last_synced_at=func.now())
                )
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("Falha ao sincronizar item %s", item_id)
                raise
        result.finished_at = datetime.now(UTC)
        logger.info("Sync concluído: %s", result.as_dict())
        return result

    # -- etapas -------------------------------------------------------------
    async def _sync_item_row(self, session: AsyncSession, item_id: str, result: SyncResult) -> None:
        raw = await self._client.request("GET", f"/items/{item_id}")
        await upsert_rows(session, Item, [map_item(raw)])
        await session.commit()

        status = str(raw.get("status") or "")
        execution = str(raw.get("executionStatus") or "")
        if status not in {"UPDATED", "UPDATING"} or execution not in {"SUCCESS", "PARTIAL_SUCCESS", ""}:
            result.warnings.append(
                f"Item com status={status or '?'} / executionStatus={execution or '?'}: "
                "os dados podem estar desatualizados (reconecte o MeuPluggy se necessário)."
            )

    async def _sync_accounts(
        self, session: AsyncSession, item_id: str, result: SyncResult
    ) -> list[_AccountRef]:
        raw_accounts = await self._client.fetch_all("/accounts", {"itemId": item_id})
        rows = [map_account(raw, item_id) for raw in raw_accounts]
        result.accounts = await upsert_rows(session, Account, rows)
        await deactivate_missing(session, Account, item_id, [row["id"] for row in rows])
        await session.commit()
        return [_AccountRef(id=row["id"], type=row["type"]) for row in rows]

    async def _sync_bills(
        self, session: AsyncSession, accounts: Sequence[_AccountRef], result: SyncResult
    ) -> None:
        for account in (a for a in accounts if a.type == "CREDIT"):
            try:
                raw_bills = await self._client.fetch_all("/bills", {"accountId": account.id})
            except PluggyAPIError as exc:
                self._soft_fail(result, "faturas", account.id, exc)
                continue
            rows = [map_bill(raw, account.id) for raw in raw_bills]
            result.bills += await upsert_rows(session, Bill, rows)
        await session.commit()

    async def _sync_transactions(
        self,
        session: AsyncSession,
        accounts: Sequence[_AccountRef],
        result: SyncResult,
        full_history: bool,
    ) -> None:
        for account in accounts:
            start = None if full_history else await self._incremental_start(session, account.id)
            params: JsonDict = {"accountId": account.id}
            if start is not None:
                params["from"] = start.isoformat()

            known_bills = set(
                await session.scalars(select(Bill.id).where(Bill.account_id == account.id))
            )
            try:
                async for page in self._client.iter_pages(
                    "/transactions", params, page_size=_TRANSACTIONS_PAGE_SIZE
                ):
                    rows = [
                        row
                        for raw in page
                        if (row := map_transaction(raw, account.id, known_bills)) is not None
                    ]
                    result.transactions += await upsert_rows(session, Transaction, rows)
                    await session.commit()  # por página: memória baixa e progresso preservado
            except PluggyAPIError as exc:
                await session.rollback()
                self._soft_fail(result, "transações", account.id, exc)

    async def _sync_investments(
        self, session: AsyncSession, item_id: str, result: SyncResult
    ) -> None:
        try:
            raw_items = await self._client.fetch_all(
                "/investments", {"itemId": item_id}, page_size=_TRANSACTIONS_PAGE_SIZE
            )
        except PluggyAPIError as exc:
            self._soft_fail(result, "investimentos", item_id, exc)
            return
        rows = [map_investment(raw, item_id) for raw in raw_items]
        result.investments = await upsert_rows(session, Investment, rows)
        await deactivate_missing(session, Investment, item_id, [row["id"] for row in rows])
        await session.commit()

    async def _sync_loans(self, session: AsyncSession, item_id: str, result: SyncResult) -> None:
        try:
            raw_loans = await self._client.fetch_all("/loans", {"itemId": item_id})
        except PluggyAPIError as exc:
            self._soft_fail(result, "empréstimos", item_id, exc)
            return
        rows = [map_loan(raw, item_id) for raw in raw_loans]
        result.loans = await upsert_rows(session, Loan, rows)
        await deactivate_missing(session, Loan, item_id, [row["id"] for row in rows])
        await session.commit()

    # -- utilitários --------------------------------------------------------
    async def _incremental_start(self, session: AsyncSession, account_id: str) -> date | None:
        """Última transação gravada menos a janela de sobreposição; None = histórico completo."""
        last = await session.scalar(
            select(func.max(Transaction.date)).where(Transaction.account_id == account_id)
        )
        if last is None:
            return None
        overlap = timedelta(days=self._settings.pluggy_transactions_overlap_days)
        return (last - overlap).date()

    @staticmethod
    def _soft_fail(result: SyncResult, what: str, ref: str, exc: PluggyAPIError) -> None:
        if exc.status_code not in _SOFT_FAIL_STATUSES:
            raise exc
        message = f"{what} indisponíveis para {ref}: HTTP {exc.status_code} ({exc.message})"
        logger.warning(message)
        result.warnings.append(message)


# ---------------------------------------------------------------------------
# 7. Entrada de linha de comando / reuso pela API
# ---------------------------------------------------------------------------
async def run_sync(item_id: str | None = None, *, full_history: bool = False) -> SyncResult:
    """Sincroniza usando as configurações do ``.env``."""
    settings = get_settings()
    target = item_id or settings.pluggy_item_id
    if not target:
        raise ValueError("Informe o itemId (argumento ou PLUGGY_ITEM_ID no .env).")
    async with PluggyClient(settings) as client:
        service = PluggySyncService(client, AsyncSessionLocal, settings)
        return await service.sync_item(target, full_history=full_history)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza um item da Pluggy com o banco local.")
    parser.add_argument("item_id", nargs="?", help="itemId (padrão: PLUGGY_ITEM_ID)")
    parser.add_argument("--full", action="store_true", help="reimporta todo o histórico")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(asyncio.run(run_sync(args.item_id, full_history=args.full)).as_dict())


if __name__ == "__main__":
    _main()
