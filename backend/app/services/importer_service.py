"""Importação de extratos OFX e CSV para a tabela ``transactions``.

Para contas/cartões que não cabem na Pluggy (limite de conexões do MeuPluggy), o usuário
sobe o extrato do banco e as linhas entram na MESMA tabela, com o mesmo formato, de modo que
o motor quantitativo as enxerga sem nenhuma alteração.

Decisões:
- **Idempotência por id determinístico.** ``Transaction.id`` é ``imp_`` + 32 hex de um SHA-256
  (36 caracteres, cabe em ``String(36)``). Reimportar o mesmo arquivo gera os mesmos ids; o
  serviço consulta quais já existem e só insere os novos (``ON CONFLICT DO NOTHING`` como
  proteção contra requisições simultâneas).
    * OFX: hash de ``conta + FITID``.
    * CSV com coluna de identificador (ex.: Nubank): hash de ``conta + identificador``.
    * CSV sem identificador: hash de ``conta + data + valor + descrição normalizada + n``, onde
      ``n`` é a ordem entre linhas idênticas DO MESMO ARQUIVO. Sem ele, duas compras iguais no
      mesmo dia (dois cafés de R$ 8,00) virariam uma só.
- **Sinal e tipo.** ``amount`` é gravado com sinal (saída < 0) e ``type`` = DEBIT/CREDIT. O motor
  usa ``abs(amount)`` + ``type``, então o que importa é o ``type`` estar certo.
- **Datas** ficam como estão escritas no arquivo (sem converter fuso), à meia-noite quando só há dia.
- **Origem** fica em ``provider_code`` (``import:ofx`` / ``import:csv``), para filtrar ou desfazer.
- Parsing é síncrono/CPU e roda em thread (``asyncio.to_thread``); o banco é 100% assíncrono.
"""

import asyncio
import csv
import hashlib
import io
import logging
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, Transaction
from app.schemas.importer import CsvOptions, ImportIssue, ImportReport, SampleRow

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES: Final = 10 * 1024 * 1024
_MAX_BIND_PARAMS: Final = 30_000
_ID_LOOKUP_CHUNK: Final = 5_000
_MAX_REPORTED_ISSUES: Final = 25
_HEADER_SEARCH_ROWS: Final = 60
_CENT: Final = Decimal("0.01")

Direction = Literal["DEBIT", "CREDIT"]


class ImportFileError(ValueError):
    """Arquivo ilegível ou não reconhecido (vira HTTP 422)."""


class AccountNotFoundError(LookupError):
    """``account_id`` inexistente no banco local (vira HTTP 404)."""


# ===========================================================================
# 1. Modelos intermediários
# ===========================================================================
@dataclass(frozen=True, slots=True)
class ParsedTransaction:
    date: datetime
    amount: Decimal  # com sinal: saída < 0
    type: Direction
    description: str
    description_raw: str | None = None
    external_id: str | None = None  # FITID (OFX) ou coluna de identificador (CSV)
    operation_type: str | None = None  # TRNTYPE (OFX)
    category: str | None = None
    currency_code: str = "BRL"


@dataclass(slots=True)
class ParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    issues: list[ImportIssue] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    detected: dict[str, str | int | None] = field(default_factory=dict)

    def skip(self, line: int | None, reason: str) -> None:
        self.skipped += 1
        if len(self.issues) < _MAX_REPORTED_ISSUES:
            self.issues.append(ImportIssue(line=line, reason=reason))


# ===========================================================================
# 2. Utilidades de texto, dinheiro e data
# ===========================================================================
def decode_text(raw: bytes) -> str:
    """UTF-16 (com BOM) -> UTF-8 -> CP1252 -> Latin-1. Extratos brasileiros misturam tudo isso."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def normalize_text(value: str) -> str:
    """Sem acento, minúsculo, espaços colapsados. Base das chaves de duplicidade."""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    return re.sub(r"\s+", " ", stripped).strip().casefold()


def _normalize_header(value: str) -> str:
    text = re.sub(r"r\$", " ", normalize_text(value))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


_MONEY_JUNK: Final = re.compile(r"[^\d.,\-+()]")


def _guess_decimal_separator(number: str) -> str:
    """Decide qual separador é o decimal em um número só com dígitos, '.' e ','.

    - Os dois presentes: o último é o decimal ("1.235,50" -> ',' ; "1,235.50" -> '.').
    - Um só tipo, repetido ("1.234.567"): é milhar, não há decimal.
    - Um só separador, uma vez: três dígitos depois dele = milhar ("1.235"); senão decimal.
    """
    has_dot, has_comma = "." in number, "," in number
    if has_dot and has_comma:
        return "," if number.rfind(",") > number.rfind(".") else "."
    if not (has_dot or has_comma):
        return "."
    sep = "," if has_comma else "."
    other = "." if sep == "," else ","
    if number.count(sep) > 1 or len(number.rsplit(sep, 1)[1]) == 3:
        return other  # `sep` é separador de milhar; não há parte decimal
    return sep


def parse_money(value: str, decimal_separator: str | None = None) -> Decimal:
    """Converte "R$ 1.235,50", "-1.235,50", "1,235.50", "(35,90)", "35.9", "1.235,50-" em Decimal.

    Levanta ``ValueError`` se não for um número. O resultado tem 2 casas (ROUND_HALF_UP).
    """
    text = value.strip()
    if not text:
        raise ValueError("valor vazio")
    negative = text.startswith("(") and text.endswith(")")
    cleaned = _MONEY_JUNK.sub("", text).replace("(", "").replace(")", "")
    if cleaned.endswith("-"):
        negative, cleaned = True, cleaned[:-1]
    if cleaned.startswith("-"):
        negative, cleaned = True, cleaned[1:]
    cleaned = cleaned.lstrip("+")
    if not re.fullmatch(r"[\d.,]+", cleaned) or not any(ch.isdigit() for ch in cleaned):
        raise ValueError(f"não é um valor monetário: {value!r}")

    decimal = decimal_separator or _guess_decimal_separator(cleaned)
    thousands = "." if decimal == "," else ","
    head, _, fraction = cleaned.rpartition(decimal) if decimal in cleaned else (cleaned, "", "")
    head = head.replace(thousands, "")
    if (head and not head.isdigit()) or (fraction and not fraction.isdigit()):
        raise ValueError(f"separadores inconsistentes: {value!r}")
    try:
        number = Decimal(f"{head or '0'}.{fraction or '0'}")
    except InvalidOperation as exc:
        raise ValueError(f"não é um valor monetário: {value!r}") from exc
    number = number.quantize(_CENT, rounding=ROUND_HALF_UP)
    return -number if negative else number


_MONTHS_PT: Final = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}  # fmt: skip
_NUMERIC_DATE: Final = re.compile(r"^\s*(\d{1,4})[/\-.](\d{1,2})[/\-.](\d{1,4})")
_TEXT_DATE: Final = re.compile(r"^\s*(\d{1,2})\s+(?:de\s+)?([a-zç]{3})[a-zç]*\.?\s+(?:de\s+)?(\d{4})", re.I)


def parse_date(value: str, date_format: str | None = None) -> datetime:
    """Datas brasileiras (dia primeiro) ou ISO; ignora a hora. Levanta ``ValueError``."""
    text = value.strip()
    if date_format:
        parsed = datetime.strptime(text, date_format)
        return parsed.replace(hour=0, minute=0, second=0, microsecond=0)
    if match := _NUMERIC_DATE.match(text):
        a, b, c = match.groups()
        if len(a) == 4:  # AAAA-MM-DD
            year, month, day = int(a), int(b), int(c)
        else:  # DD/MM/AAAA ou DD/MM/AA
            day, month, year = int(a), int(b), int(c)
            if len(c) <= 2:
                year += 2000
        return datetime(year, month, day)
    if match := _TEXT_DATE.match(text):
        month = _MONTHS_PT.get(normalize_text(match.group(2))[:3])
        if month:
            return datetime(int(match.group(3)), month, int(match.group(1)))
    raise ValueError(f"data não reconhecida: {value!r}")


def _clip(value: str | None, size: int) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:size] or None


# ===========================================================================
# 3. Parser OFX
# ===========================================================================
_OFX_HEADER: Final = (
    "OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:UTF-8\n"
    "CHARSET:NONE\nCOMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n"
)
# DTPOSTED=20240105000000[-3:BRT]: tiramos o fuso para manter o dia escrito pelo banco
# (o ofxparse converteria para UTC e lançamentos após 21h cairiam no dia seguinte).
_OFX_TZ_SUFFIX: Final = re.compile(r"(<DT[A-Z]*>\s*\d{8,14}(?:\.\d+)?)\s*\[[^\]]*\]", re.I)
_OFX_DEBIT_TYPES: Final = frozenset(
    {"DEBIT", "PAYMENT", "CHECK", "ATM", "POS", "FEE", "SRVCHG", "DIRECTDEBIT", "REPEATPMT", "CASH"}
)
_OFX_CREDIT_TYPES: Final = frozenset({"CREDIT", "DEP", "DIRECTDEP", "INT", "DIV"})


def _prepare_ofx(raw: bytes) -> bytes:
    """Normaliza codificação e cabeçalho: o ofxparse quebra sem ENCODING ou com acentos."""
    text = decode_text(raw)
    start = re.search(r"<OFX[\s>]", text, re.I)
    if start is None:
        raise ImportFileError("Arquivo não parece ser OFX (tag <OFX> não encontrada).")
    body = _OFX_TZ_SUFFIX.sub(r"\1", text[start.start() :])
    return (_OFX_HEADER + body).encode("utf-8")


def _resolve_direction(amount: Decimal, trn_type: str) -> tuple[Decimal, Direction, bool]:
    """(valor com sinal, DEBIT/CREDIT, sinal_foi_corrigido).

    Se o TRNTYPE contradiz o sinal (ex.: DEBIT com valor positivo, comum em extratos de cartão),
    confiamos no TRNTYPE e corrigimos o sinal.
    """
    if trn_type in _OFX_DEBIT_TYPES and amount > 0:
        return -amount, "DEBIT", True
    if trn_type in _OFX_CREDIT_TYPES and amount < 0:
        return -amount, "CREDIT", True
    if amount < 0:
        return amount, "DEBIT", False
    if amount > 0:
        return amount, "CREDIT", False
    return amount, ("DEBIT" if trn_type in _OFX_DEBIT_TYPES else "CREDIT"), False


def parse_ofx(raw: bytes) -> ParseResult:
    """Lê um extrato OFX (1.x SGML ou 2.x XML) de uma única conta/cartão."""
    from ofxparse import OfxParser  # import tardio: o módulo é opcional até o primeiro uso

    result = ParseResult()
    try:
        ofx = OfxParser.parse(io.BytesIO(_prepare_ofx(raw)), fail_fast=False)
    except ImportFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - o ofxparse levanta tipos variados em arquivos ruins
        raise ImportFileError(f"Não foi possível ler o OFX: {exc}") from exc

    statements = [acc for acc in ofx.accounts if getattr(acc, "statement", None) is not None]
    if not statements:
        raise ImportFileError("O OFX não contém nenhum extrato (STMTRS/CCSTMTRS).")
    if len(statements) > 1:
        numbers = ", ".join(str(acc.account_id) for acc in statements)
        raise ImportFileError(
            f"O OFX tem {len(statements)} contas ({numbers}). Importe um arquivo por conta/cartão."
        )

    ofx_account = statements[0]
    statement = ofx_account.statement
    currency = str(getattr(statement, "currency", None) or getattr(ofx_account, "curdef", None) or "BRL")
    result.detected = {
        "ofx_account_id": str(ofx_account.account_id or "") or None,
        "ofx_bank_id": str(getattr(ofx_account, "routing_number", "") or "") or None,
        "currency": currency[:3].upper(),
    }

    for message in getattr(statement, "warnings", []) or []:
        result.warnings.append(f"OFX: {message}")
    for entry in getattr(statement, "discarded_entries", []) or []:
        result.skip(None, f"lançamento descartado pelo leitor OFX: {entry}")

    corrected = 0
    for position, trn in enumerate(statement.transactions, start=1):
        if trn.date is None or trn.amount is None:
            result.skip(position, "lançamento sem data (DTPOSTED) ou valor (TRNAMT)")
            continue
        try:
            amount = Decimal(str(trn.amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            result.skip(position, f"valor inválido: {trn.amount!r}")
            continue
        if amount == 0:
            result.skip(position, "valor zero")
            continue

        trn_type = str(getattr(trn, "type", "") or "").upper()
        amount, direction, flipped = _resolve_direction(amount, trn_type)
        corrected += flipped

        memo = _clip(getattr(trn, "memo", None), 512)
        payee = _clip(getattr(trn, "payee", None), 512)
        parts = [p for p in (memo, payee) if p]
        distinct = list(dict.fromkeys(parts))  # memo e nome idênticos não duplicam
        result.transactions.append(
            ParsedTransaction(
                date=trn.date.replace(tzinfo=None) if trn.date.tzinfo else trn.date,
                amount=amount,
                type=direction,
                description=memo or payee or "(sem descrição)",
                description_raw=_clip(" | ".join(distinct), 512) if len(distinct) > 1 else None,
                external_id=_clip(str(getattr(trn, "id", "") or ""), 255),
                operation_type=_clip(trn_type or None, 64),
                currency_code=currency[:3].upper(),
            )
        )

    if corrected:
        result.warnings.append(
            f"{corrected} lançamento(s) tinham sinal contrário ao TRNTYPE (ex.: DEBIT positivo); "
            "o sinal foi ajustado conforme o TRNTYPE."
        )
    return result


# ===========================================================================
# 4. Parser CSV
# ===========================================================================
_DATE_ALIASES: Final = (
    "data", "date", "data lancamento", "data de lancamento", "data da compra", "data compra",
    "data movimentacao", "data transacao", "data da transacao", "dt lancamento",
)  # fmt: skip
_DESCRIPTION_ALIASES: Final = (
    "descricao", "historico", "lancamento", "estabelecimento", "title", "titulo", "memo",
    "detalhes", "detalhe", "descricao da transacao", "description", "nome",
)  # fmt: skip
_AMOUNT_ALIASES: Final = (
    "valor", "amount", "quantia", "valor liquido", "valor da transacao", "valor transacao",
)  # fmt: skip
_DEBIT_ALIASES: Final = ("debito", "saida", "saidas", "valor debito", "debit")
_CREDIT_ALIASES: Final = ("credito", "entrada", "entradas", "valor credito", "credit")
_ID_ALIASES: Final = ("identificador", "id", "id transacao", "codigo transacao", "fitid", "nsu")
_CATEGORY_ALIASES: Final = ("categoria", "category")
_DELIMITERS: Final = (";", ",", "\t", "|")


@dataclass(frozen=True, slots=True)
class _Columns:
    date: int
    description: int | None
    amount: int | None
    debit: int | None
    credit: int | None
    identifier: int | None
    category: int | None
    names: dict[str, str]


def _resolve_columns(cells: Sequence[str], options: CsvOptions) -> _Columns | None:
    """Tenta ler ``cells`` como cabeçalho; None se não houver data e valor reconhecíveis."""
    normalized = [_normalize_header(cell) for cell in cells]

    def find(explicit: str | None, aliases: Sequence[str]) -> int | None:
        if explicit:
            target = _normalize_header(explicit)
            return normalized.index(target) if target in normalized else None
        for alias in aliases:
            if alias in normalized:
                return normalized.index(alias)
        return None

    date_idx = find(options.date_column, _DATE_ALIASES)
    amount_idx = find(options.amount_column, _AMOUNT_ALIASES)
    debit_idx = None if options.amount_column else find(options.debit_column, _DEBIT_ALIASES)
    credit_idx = None if options.amount_column else find(options.credit_column, _CREDIT_ALIASES)
    if date_idx is None or (amount_idx is None and debit_idx is None and credit_idx is None):
        return None
    if amount_idx is not None:
        debit_idx = credit_idx = None  # coluna de valor com sinal tem prioridade

    desc_idx = find(options.description_column, _DESCRIPTION_ALIASES)
    id_idx = find(options.id_column, _ID_ALIASES)
    category_idx = find(options.category_column, _CATEGORY_ALIASES)

    def label(index: int | None) -> str:
        return cells[index].strip() if index is not None else ""

    return _Columns(
        date=date_idx,
        description=desc_idx,
        amount=amount_idx,
        debit=debit_idx,
        credit=credit_idx,
        identifier=id_idx,
        category=category_idx,
        names={
            "date_column": label(date_idx),
            "description_column": label(desc_idx),
            "amount_column": label(amount_idx),
            "debit_column": label(debit_idx),
            "credit_column": label(credit_idx),
            "id_column": label(id_idx),
            "category_column": label(category_idx),
        },
    )


def _find_header(text: str, options: CsvOptions) -> tuple[str, int, _Columns]:
    """Procura delimitador + linha de cabeçalho (extratos têm preâmbulo com dados da conta)."""
    candidates = (options.delimiter,) if options.delimiter else _DELIMITERS
    first_rows: list[str] = []
    for delimiter in candidates:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)
        for index, row in enumerate(reader):
            if index >= _HEADER_SEARCH_ROWS:
                break
            if not first_rows and any(cell.strip() for cell in row):
                first_rows = [cell.strip() for cell in row]
            columns = _resolve_columns(row, options)
            if columns is not None:
                return delimiter, reader.line_num, columns
    seen = f" Primeira linha lida: {first_rows}." if first_rows else ""
    raise ImportFileError(
        "Não encontrei o cabeçalho do CSV (precisa de uma coluna de data e uma de valor, "
        "ou de entradas/saídas). Informe date_column, amount_column e description_column."
        + seen
    )


def _cell(row: Sequence[str], index: int | None) -> str:
    return row[index].strip() if index is not None and index < len(row) else ""


def _iter_rows(text: str, delimiter: str, after_line: int) -> Iterator[tuple[int, list[str]]]:
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)
    for row in reader:
        if reader.line_num > after_line and any(cell.strip() for cell in row):
            yield reader.line_num, row


def parse_csv(raw: bytes, options: CsvOptions, *, account_type: str) -> ParseResult:
    """Lê um CSV de extrato bancário/cartão brasileiro com mapeamento flexível de colunas."""
    text = decode_text(raw)
    delimiter, header_line, columns = _find_header(text, options)

    invert = options.sign == "invert" or (options.sign == "auto" and account_type == "CREDIT")
    result = ParseResult(
        detected={
            "delimiter": "TAB" if delimiter == "\t" else delimiter,
            "header_line": header_line,
            "sign": "invert" if invert else "as_is",
            "decimal_separator": options.decimal_separator,
            **columns.names,
        }
    )
    if columns.description is None:
        result.warnings.append("Coluna de descrição não encontrada; usando '(sem descrição)'.")
    if columns.amount is not None and options.sign == "auto" and account_type == "CREDIT":
        result.warnings.append(
            "Conta de cartão: valores positivos foram tratados como compra (saída) e negativos "
            "como pagamento/estorno. Se o seu extrato for o contrário, reenvie com sign=as_is."
        )

    balance_rows = 0
    for line, row in _iter_rows(text, delimiter, header_line):
        description = _cell(row, columns.description)
        if re.match(r"saldo\b", normalize_text(description)):
            balance_rows += 1
            continue

        try:
            when = parse_date(_cell(row, columns.date), options.date_format)
        except ValueError as exc:
            result.skip(line, str(exc))
            continue

        try:
            amount = _row_amount(row, columns, options.decimal_separator)
        except ValueError as exc:
            result.skip(line, str(exc))
            continue
        if amount is None:
            result.skip(line, "sem valor")
            continue
        if amount == 0:
            result.skip(line, "valor zero")
            continue
        if invert and columns.amount is not None:
            amount = -amount

        result.transactions.append(
            ParsedTransaction(
                date=when,
                amount=amount,
                type="DEBIT" if amount < 0 else "CREDIT",
                description=_clip(description, 512) or "(sem descrição)",
                external_id=_clip(_cell(row, columns.identifier), 255),
                category=_clip(_cell(row, columns.category), 120),
            )
        )

    if balance_rows:
        result.warnings.append(f"{balance_rows} linha(s) de saldo ignoradas.")
    return result


def _row_amount(row: Sequence[str], columns: _Columns, decimal_separator: str | None) -> Decimal | None:
    """Valor com sinal da linha, ou None se estiver vazio. ``ValueError`` se ilegível."""
    if columns.amount is not None:
        cell = _cell(row, columns.amount)
        return parse_money(cell, decimal_separator) if cell else None

    debit_cell, credit_cell = _cell(row, columns.debit), _cell(row, columns.credit)
    if not debit_cell and not credit_cell:
        return None
    debit = abs(parse_money(debit_cell, decimal_separator)) if debit_cell else Decimal("0.00")
    credit = abs(parse_money(credit_cell, decimal_separator)) if credit_cell else Decimal("0.00")
    return credit - debit


# ===========================================================================
# 5. Chaves de idempotência e persistência
# ===========================================================================
def _hash_id(*parts: str) -> str:
    """``imp_`` + 32 hex (128 bits do SHA-256) = 36 caracteres."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"imp_{digest[:32]}"


def build_rows(
    account_id: str, parsed: Sequence[ParsedTransaction], source: Literal["ofx", "csv"]
) -> tuple[list[dict[str, Any]], int, int]:
    """Gera as linhas de ``transactions`` com ids determinísticos.

    Retorna ``(linhas, repetidas_no_arquivo, colisoes_de_id)``.
    - Com id externo: o mesmo id com os mesmos dados é repetição; o mesmo id com dados
      diferentes (banco que reaproveita FITID) ganha um sufixo de ordem e conta como colisão.
    - Sem id externo: linhas idênticas são lançamentos distintos (numeradas pela ordem).
    """
    rows: list[dict[str, Any]] = []
    signatures: dict[str, list[tuple[str, str, str]]] = {}
    occurrences: Counter[str] = Counter()
    in_file_duplicates = 0
    collisions = 0

    for item in parsed:
        description_key = normalize_text(item.description)
        day = item.date.date().isoformat()
        amount_key = f"{item.amount:.2f}"

        if item.external_id:
            base = f"id|{account_id}|{item.external_id}"
            signature = (day, amount_key, description_key)
            seen = signatures.setdefault(base, [])
            if signature in seen:
                in_file_duplicates += 1
                continue
            seen.append(signature)
            ordinal = len(seen) - 1
            if ordinal:
                collisions += 1
            tx_id = _hash_id(base, str(ordinal)) if ordinal else _hash_id(base)
        else:
            base = f"row|{account_id}|{day}|{amount_key}|{description_key}"
            tx_id = _hash_id(base, str(occurrences[base]))
            occurrences[base] += 1

        rows.append(
            {
                "id": tx_id,
                "account_id": account_id,
                "date": item.date,
                "description": item.description,
                "description_raw": item.description_raw,
                "amount": item.amount,
                "currency_code": item.currency_code,
                "type": item.type,
                "status": "POSTED",
                "operation_type": item.operation_type,
                "category": item.category,
                "provider_code": f"import:{source}",
            }
        )
    return rows, in_file_duplicates, collisions


async def _existing_ids(session: AsyncSession, ids: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(ids), _ID_LOOKUP_CHUNK):
        chunk = list(ids[start : start + _ID_LOOKUP_CHUNK])
        found.update(await session.scalars(select(Transaction.id).where(Transaction.id.in_(chunk))))
    return found


async def _insert_new(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> None:
    """INSERT ... ON CONFLICT (id) DO NOTHING, em lotes. SQLite e PostgreSQL."""
    if not rows:
        return
    dialect = session.bind.dialect.name  # type: ignore[union-attr]
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        raise NotImplementedError(f"Importação não implementada para o dialeto '{dialect}'.")

    chunk_size = max(1, _MAX_BIND_PARAMS // len(rows[0]))
    for start in range(0, len(rows), chunk_size):
        statement = dialect_insert(Transaction).values(list(rows[start : start + chunk_size]))
        await session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))


async def _get_account(session: AsyncSession, account_id: str) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError(f"Conta '{account_id}' não encontrada.")
    return account


async def _persist(
    session: AsyncSession,
    account: Account,
    parsed: ParseResult,
    *,
    source: Literal["ofx", "csv"],
    filename: str | None,
    dry_run: bool,
) -> ImportReport:
    if not parsed.transactions:
        raise ImportFileError(
            "Nenhum lançamento válido encontrado no arquivo."
            + (f" Primeiro problema: {parsed.issues[0].reason}." if parsed.issues else "")
        )

    rows, in_file_duplicates, collisions = build_rows(account.id, parsed.transactions, source)
    existing = await _existing_ids(session, [row["id"] for row in rows])
    new_rows = [row for row in rows if row["id"] not in existing]

    if not dry_run and new_rows:
        try:
            await _insert_new(session, new_rows)
            await session.commit()
        except Exception:
            await session.rollback()
            raise

    warnings = list(parsed.warnings)
    if collisions:
        warnings.append(
            f"{collisions} lançamento(s) repetiam o mesmo identificador com dados diferentes "
            "(o banco reaproveita FITID); foram mantidos como lançamentos distintos."
        )
    currencies = {tx.currency_code for tx in parsed.transactions}
    if currencies - {account.currency_code}:
        warnings.append(
            f"Moeda do arquivo ({', '.join(sorted(currencies))}) difere da conta ({account.currency_code})."
        )
    ofx_number = parsed.detected.get("ofx_account_id")
    if source == "ofx" and ofx_number and account.number:
        digits = re.sub(r"\D", "", str(ofx_number))[-4:]
        if digits and digits not in re.sub(r"\D", "", account.number):
            warnings.append(
                f"O número da conta no OFX ({ofx_number}) não parece o da conta de destino "
                f"({account.number}). Confira se escolheu o account_id certo."
            )

    dates = [tx.date for tx in parsed.transactions]
    income = sum((r["amount"] for r in new_rows if r["amount"] > 0), Decimal("0"))
    expense = sum((-r["amount"] for r in new_rows if r["amount"] < 0), Decimal("0"))
    return ImportReport(
        source=source,
        account_id=account.id,
        filename=filename,
        dry_run=dry_run,
        rows_read=len(parsed.transactions),
        inserted=len(new_rows),
        duplicates=len(rows) - len(new_rows),
        duplicates_in_file=in_file_duplicates,
        skipped=parsed.skipped,
        period_start=min(dates),
        period_end=max(dates),
        inserted_income=float(income),
        inserted_expense=float(expense),
        detected=parsed.detected,
        issues=parsed.issues,
        warnings=warnings,
        sample=(
            [
                SampleRow(date=t.date, description=t.description, amount=float(t.amount), type=t.type)
                for t in parsed.transactions[:5]
            ]
            if dry_run
            else []
        ),
    )


# ===========================================================================
# 6. API do serviço
# ===========================================================================
async def import_ofx(
    session: AsyncSession,
    account_id: str,
    content: bytes,
    *,
    filename: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    account = await _get_account(session, account_id)
    parsed = await asyncio.to_thread(parse_ofx, content)
    return await _persist(session, account, parsed, source="ofx", filename=filename, dry_run=dry_run)


async def import_csv(
    session: AsyncSession,
    account_id: str,
    content: bytes,
    options: CsvOptions | None = None,
    *,
    filename: str | None = None,
    dry_run: bool = False,
) -> ImportReport:
    account = await _get_account(session, account_id)
    parsed = await asyncio.to_thread(
        parse_csv, content, options or CsvOptions(), account_type=account.type
    )
    return await _persist(session, account, parsed, source="csv", filename=filename, dry_run=dry_run)
