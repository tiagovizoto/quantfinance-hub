"""Modelos ORM (SQLAlchemy 2.0 declarativo tipado).

Decisões de modelagem:
- Chaves primárias são os UUIDs da Pluggy (``String(36)``): o upsert é idempotente.
- Valores monetários: ``Numeric(15, 2)``. Taxas/percentuais usam ``Numeric(14, 6)``
  (2 casas não bastam para CET mensal, % do CDI etc.).
- Campos "enum" ficam como ``String`` (sem tipo ENUM nativo) para manter a
  migração SQLite -> PostgreSQL trivial e evitar ALTER TYPE.
- Datas vindas da Pluggy são normalizadas para UTC *naive* no serviço de sync.
- Payloads variáveis (finance charges, metadata etc.) ficam em ``JSON``.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    func,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY = Numeric(15, 2)
RATE = Numeric(14, 6)
QUANTITY = Numeric(24, 8)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Item(TimestampMixin, Base):
    """Conexão Pluggy (um ``itemId`` = uma instituição conectada, ex.: MeuPluggy)."""

    __tablename__ = "items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connector_id: Mapped[int | None] = mapped_column(Integer)
    connector_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str | None] = mapped_column(String(32))
    execution_status: Mapped[str | None] = mapped_column(String(32))
    pluggy_created_at: Mapped[datetime | None] = mapped_column(DateTime)
    pluggy_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    pluggy_last_updated_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    accounts: Mapped[list["Account"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )
    investments: Mapped[list["Investment"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )
    loans: Mapped[list["Loan"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", passive_deletes=True
    )


class Account(TimestampMixin, Base):
    """Conta corrente/poupança (BANK) ou cartão de crédito (CREDIT)."""

    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(16))  # BANK | CREDIT
    subtype: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    marketing_name: Mapped[str | None] = mapped_column(String(255))
    number: Mapped[str | None] = mapped_column(String(64))
    currency_code: Mapped[str] = mapped_column(String(3), default="BRL")

    balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    # Cheque especial (bankData)
    overdraft_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    overdraft_used: Mapped[Decimal | None] = mapped_column(MONEY)

    # Cartão de crédito (creditData)
    credit_level: Mapped[str | None] = mapped_column(String(64))
    credit_brand: Mapped[str | None] = mapped_column(String(64))
    credit_balance_close_date: Mapped[date | None] = mapped_column(Date)
    credit_balance_due_date: Mapped[date | None] = mapped_column(Date)
    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    credit_available_limit: Mapped[Decimal | None] = mapped_column(MONEY)
    credit_minimum_payment: Mapped[Decimal | None] = mapped_column(MONEY)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    item: Mapped["Item"] = relationship(back_populates="accounts")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )
    bills: Mapped[list["Bill"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True
    )


class Bill(TimestampMixin, Base):
    """Fatura de cartão de crédito."""

    __tablename__ = "bills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    total_amount: Mapped[Decimal] = mapped_column(MONEY)
    total_amount_currency_code: Mapped[str] = mapped_column(String(3), default="BRL")
    minimum_payment_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    allows_installments: Mapped[bool | None] = mapped_column(Boolean)
    # Encargos de financiamento (juros, IOF, multa...): lista bruta + soma.
    finance_charges: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    finance_charges_total: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    account: Mapped["Account"] = relationship(back_populates="bills")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="bill")


class Transaction(TimestampMixin, Base):
    """Movimentação de conta ou lançamento de cartão.

    ``amount`` é guardado como a Pluggy entrega (com sinal). Use ``type``
    (DEBIT/CREDIT) para a direção do fluxo ao calcular métricas.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_account_date", "account_id", "date"),
        Index("ix_transactions_category_date", "category", "date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE")
    )
    bill_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("bills.id", ondelete="SET NULL"), index=True
    )
    date: Mapped[datetime] = mapped_column(DateTime)
    description: Mapped[str | None] = mapped_column(String(512))
    description_raw: Mapped[str | None] = mapped_column(String(512))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    amount_in_account_currency: Mapped[Decimal | None] = mapped_column(MONEY)
    currency_code: Mapped[str] = mapped_column(String(3), default="BRL")
    type: Mapped[str] = mapped_column(String(8))  # DEBIT | CREDIT
    status: Mapped[str | None] = mapped_column(String(16))  # PENDING | POSTED
    operation_type: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(120))
    category_id: Mapped[str | None] = mapped_column(String(32))
    provider_code: Mapped[str | None] = mapped_column(String(64))
    balance: Mapped[Decimal | None] = mapped_column(MONEY)
    installment_number: Mapped[int | None] = mapped_column(Integer)
    total_installments: Mapped[int | None] = mapped_column(Integer)
    payment_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    credit_card_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    merchant: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    account: Mapped["Account"] = relationship(back_populates="transactions")
    bill: Mapped["Bill | None"] = relationship(back_populates="transactions")


class Investment(TimestampMixin, Base):
    """Posição consolidada (renda fixa, variável, fundos...)."""

    __tablename__ = "investments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str | None] = mapped_column(String(32))  # FIXED_INCOME, EQUITY, MUTUAL_FUND...
    subtype: Mapped[str | None] = mapped_column(String(48))  # CDB, LCI, LCA, TREASURY, STOCK...
    # Classe normalizada: RENDA_FIXA | RENDA_VARIAVEL | FUNDOS | OUTROS
    asset_class: Mapped[str] = mapped_column(String(24), default="OUTROS", index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(16))
    issuer: Mapped[str | None] = mapped_column(String(255))
    institution_name: Mapped[str | None] = mapped_column(String(255))
    currency_code: Mapped[str] = mapped_column(String(3), default="BRL")
    status: Mapped[str | None] = mapped_column(String(32))

    amount_invested: Mapped[Decimal | None] = mapped_column(MONEY)  # valor aplicado
    gross_balance: Mapped[Decimal | None] = mapped_column(MONEY)  # saldo bruto
    net_balance: Mapped[Decimal | None] = mapped_column(MONEY)  # bruto - impostos
    taxes: Mapped[Decimal | None] = mapped_column(MONEY)
    profit: Mapped[Decimal | None] = mapped_column(MONEY)
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit_value: Mapped[Decimal | None] = mapped_column(QUANTITY)

    # Indexador: CDI | SELIC | IPCA | PRE | ...; `rate` = % do indexador (ex.: 110 = 110% CDI)
    indexer: Mapped[str | None] = mapped_column(String(16), index=True)
    rate: Mapped[Decimal | None] = mapped_column(RATE)
    fixed_annual_rate: Mapped[Decimal | None] = mapped_column(RATE)
    annual_rate: Mapped[Decimal | None] = mapped_column(RATE)
    last_month_rate: Mapped[Decimal | None] = mapped_column(RATE)
    last_twelve_months_rate: Mapped[Decimal | None] = mapped_column(RATE)

    issue_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date, index=True)
    position_date: Mapped[date | None] = mapped_column(Date)

    raw_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    item: Mapped["Item"] = relationship(back_populates="investments")


class Loan(TimestampMixin, Base):
    """Empréstimo ou financiamento."""

    __tablename__ = "loans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("items.id", ondelete="CASCADE"), index=True
    )
    ipoc_code: Mapped[str | None] = mapped_column(String(128))
    product_name: Mapped[str | None] = mapped_column(String(255))
    type: Mapped[str | None] = mapped_column(String(64))
    contract_number: Mapped[str | None] = mapped_column(String(128))
    currency_code: Mapped[str] = mapped_column(String(3), default="BRL")

    contract_date: Mapped[date | None] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    first_installment_due_date: Mapped[date | None] = mapped_column(Date)
    disbursement_dates: Mapped[list[Any] | None] = mapped_column(JSON)

    contract_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    outstanding_balance: Mapped[Decimal | None] = mapped_column(MONEY)

    # CET em % a.a. (como a Pluggy entrega) e % a.m. equivalente (composto).
    cet_annual: Mapped[Decimal | None] = mapped_column(RATE)
    cet_monthly: Mapped[Decimal | None] = mapped_column(RATE)

    amortization_scheduled: Mapped[str | None] = mapped_column(String(32))  # SAC | PRICE | ...
    installment_periodicity: Mapped[str | None] = mapped_column(String(32))
    total_installments: Mapped[int | None] = mapped_column(Integer)
    paid_installments: Mapped[int | None] = mapped_column(Integer)
    remaining_installments: Mapped[int | None] = mapped_column(Integer)
    due_installments: Mapped[int | None] = mapped_column(Integer)
    past_due_installments: Mapped[int | None] = mapped_column(Integer)

    interest_rates: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    contracted_fees: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    contracted_finance_charges: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    warranties: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)

    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )

    item: Mapped["Item"] = relationship(back_populates="loans")


class NetWorthSnapshot(Base):
    """Foto diária do patrimônio, gravada a cada sincronização (o histórico só existe daí em diante)."""

    __tablename__ = "net_worth_snapshots"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    net_worth: Mapped[Decimal] = mapped_column(MONEY)
    liquidity: Mapped[Decimal] = mapped_column(MONEY)  # saldo em contas
    investments: Mapped[Decimal] = mapped_column(MONEY)
    loans: Mapped[Decimal] = mapped_column(MONEY)  # saldo devedor de empréstimos (positivo)
    card_debt: Mapped[Decimal] = mapped_column(MONEY)  # cartão em uso (positivo)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
