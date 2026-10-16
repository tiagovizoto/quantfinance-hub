"""Schemas da importação de extratos (OFX/CSV)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class CsvOptions(BaseModel):
    """Mapeamento opcional de colunas e formatos do CSV.

    Tudo é detectado automaticamente; só preencha o que a detecção errar. Os nomes de
    coluna são comparados sem acento, sem maiúsculas e sem "R$" (ex.: ``Descrição`` = ``descricao``).
    """

    date_column: str | None = Field(None, description="Coluna da data (ex.: 'Data').")
    description_column: str | None = Field(None, description="Coluna da descrição/estabelecimento.")
    amount_column: str | None = Field(None, description="Coluna do valor (com sinal).")
    debit_column: str | None = Field(None, description="Coluna de saídas, se o banco separar débito/crédito.")
    credit_column: str | None = Field(None, description="Coluna de entradas, se o banco separar débito/crédito.")
    id_column: str | None = Field(None, description="Coluna com identificador único da transação, se existir.")
    category_column: str | None = Field(None, description="Coluna de categoria, se existir.")
    delimiter: str | None = Field(None, description="Separador de colunas; vazio = detectar (; , tab |).")
    decimal_separator: Literal[",", "."] | None = Field(
        None, description="Separador decimal dos valores; vazio = detectar por valor."
    )
    date_format: str | None = Field(
        None, description="Formato strptime (ex.: '%d/%m/%Y'); vazio = detectar (dia primeiro)."
    )
    sign: Literal["auto", "as_is", "invert"] = Field(
        "auto",
        description=(
            "'as_is': negativo = saída. 'invert': positivo = saída (cartões costumam vir assim). "
            "'auto': as_is para conta corrente e invert para cartão de crédito."
        ),
    )

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object, info: ValidationInfo) -> object:
        """Campos de formulário vazios chegam como ''; tratamos como não informados."""
        if isinstance(value, str) and not value.strip():
            return "auto" if info.field_name == "sign" else None
        return value


class ImportIssue(BaseModel):
    line: int | None = Field(None, description="Linha do arquivo (CSV) ou posição (OFX), quando conhecida.")
    reason: str


class SampleRow(BaseModel):
    date: datetime
    description: str
    amount: float
    type: Literal["DEBIT", "CREDIT"]


class ImportReport(BaseModel):
    source: Literal["ofx", "csv"]
    account_id: str
    filename: str | None
    dry_run: bool = Field(description="True = nada foi gravado; os números mostram o que aconteceria.")

    rows_read: int = Field(description="Lançamentos válidos lidos do arquivo.")
    inserted: int = Field(description="Lançamentos novos gravados.")
    duplicates: int = Field(description="Já existiam no banco (importação repetida ou arquivos que se sobrepõem).")
    duplicates_in_file: int = Field(description="Repetidos dentro do próprio arquivo (mesmo FITID e mesmos dados).")
    skipped: int = Field(description="Linhas inválidas ignoradas (data/valor ilegível, valor zero...).")

    period_start: datetime | None
    period_end: datetime | None
    inserted_income: float = Field(description="Soma das entradas novas.")
    inserted_expense: float = Field(description="Soma das saídas novas (positivo).")

    detected: dict[str, str | int | None] = Field(default_factory=dict, description="O que foi detectado no arquivo.")
    issues: list[ImportIssue] = Field(default_factory=list, description="Primeiras linhas ignoradas e o motivo.")
    warnings: list[str] = Field(default_factory=list)
    sample: list[SampleRow] = Field(default_factory=list, description="Só em dry_run: primeiros lançamentos lidos.")
