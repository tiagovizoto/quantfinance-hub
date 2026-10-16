"""Testes do módulo de importação OFX/CSV (parsers, idempotência e endpoints)."""

from datetime import datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Transaction
from app.schemas.importer import CsvOptions
from app.services import importer_service as svc
from app.services.importer_service import ImportFileError, parse_csv, parse_date, parse_money, parse_ofx

# ---------------------------------------------------------------------------
# Fixtures de arquivo
# ---------------------------------------------------------------------------
OFX_BANK = """OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20260105120000[-3:BRT]<LANGUAGE>POR</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>BRL
<BANKACCTFROM><BANKID>0341<ACCTID>12345-6<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20260101<DTEND>20260131
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260105233000[-3:BRT]<TRNAMT>-1235,50<FITID>A1<MEMO>PADARIA PÃO QUENTE</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260110<TRNAMT>5000.00<FITID>A2<NAME>SALARIO<MEMO>SALÁRIO EMPRESA</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260112000000<TRNAMT>35.90<FITID>A3<MEMO>COMPRA POSITIVA</STMTTRN>
<STMTTRN><TRNTYPE>XFER<DTPOSTED>20260115<TRNAMT>-100.00<FITID>A4<MEMO>PIX JOAO</STMTTRN>
<STMTTRN><TRNTYPE>XFER<DTPOSTED>20260116<TRNAMT>-50.00<FITID>A4<MEMO>PIX MARIA</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20260110<TRNAMT>5000.00<FITID>A2<NAME>SALARIO<MEMO>SALÁRIO EMPRESA</STMTTRN>
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260120<TRNAMT>0.00<FITID>A6<MEMO>ZERO</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>3000.00<DTASOF>20260131</LEDGERBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1></OFX>
""".encode("cp1252")

OFX_CARD_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
<CREDITCARDMSGSRSV1><CCSTMTTRNRS><TRNUID>1</TRNUID><STATUS><CODE>0</CODE><SEVERITY>INFO</SEVERITY></STATUS>
<CCSTMTRS><CURDEF>BRL</CURDEF><CCACCTFROM><ACCTID>5555</ACCTID></CCACCTFROM>
<BANKTRANLIST><DTSTART>20260101</DTSTART><DTEND>20260131</DTEND>
<STMTTRN><TRNTYPE>DEBIT</TRNTYPE><DTPOSTED>20260103</DTPOSTED><TRNAMT>-89.90</TRNAMT><FITID>C1</FITID><MEMO>Mercado Livre</MEMO></STMTTRN>
<STMTTRN><TRNTYPE>CREDIT</TRNTYPE><DTPOSTED>20260120</DTPOSTED><TRNAMT>500.00</TRNAMT><FITID>C2</FITID><MEMO>Pagamento recebido</MEMO></STMTTRN>
</BANKTRANLIST></CCSTMTRS></CCSTMTTRNRS></CREDITCARDMSGSRSV1></OFX>
"""

CSV_NUBANK_BANK = (
    "Data,Valor,Identificador,Descrição\n"
    "05/01/2026,-35.90,aaaa-1,Compra no débito - Padaria\n"
    "10/01/2026,5000.00,aaaa-2,Transferência recebida - Empresa\n"
    "11/01/2026,-12.00,aaaa-3,Café\n"
    "11/01/2026,-12.00,aaaa-4,Café\n"
).encode()

CSV_NUBANK_CARD = (
    b"date,category,title,amount\n"
    b"2026-01-05,restaurante,Padaria,35.90\n"
    b"2026-01-05,restaurante,Padaria,35.90\n"
    b"2026-01-10,pagamento,Pagamento recebido,-1000.00\n"
)

CSV_ITAU = (
    "data;lançamento;ag./origem;valor;saldos\n"
    "02/01/2026;SALDO ANTERIOR;;;1.000,00\n"
    "05/01/2026;PIX QRS PADARIA;;-1.235,50;\n"
    "06/01/2026;SISPAG SALARIO;;5.000,00;\n"
    "07/01/2026;SALDO TOTAL DISPONÍVEL DIA;;;4.000,00\n"
).encode("cp1252")

CSV_INTER = (
    "Extrato Conta Corrente\n"
    "Conta;12345-6\n"
    "Período;01/01/2026 a 31/01/2026\n"
    "\n"
    "Data Lançamento;Histórico;Descrição;Valor;Saldo por transação\n"
    '05/01/2026;Pix enviado;"PIX ENVIADO: PADARIA, LTDA";"R$ -1.235,50";"R$ 100,00"\n'
    '06/01/2026;Pix recebido;PIX RECEBIDO;"R$ 25,00";"R$ 125,00"\n'
    '07/01/2026;Compra;COMPRA DEBITO;"-R$ 25,10";"R$ 99,90"\n'
).encode()

CSV_C6 = (
    "Data Lançamento,Data Contabilização,Título,Descrição,Entrada(R$),Saída(R$),Saldo do Dia(R$)\n"
    '05/01/2026,05/01/2026,Pix,Pix enviado,0,"1.235,50","100,00"\n'
    '06/01/2026,06/01/2026,Salário,Salário,"5.000,00",0,"5.100,00"\n'
).encode()


async def count_transactions(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Transaction)) or 0)


# ---------------------------------------------------------------------------
# Valores e datas
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("R$ 1.235,50", "1235.50"),
        ("-1.235,50", "-1235.50"),
        ("-R$ 25,10", "-25.10"),
        ("R$ -25,10", "-25.10"),
        ("1,235.50", "1235.50"),
        ("-35.9", "-35.90"),
        ("35,9", "35.90"),
        ("(35,90)", "-35.90"),
        ("1.235,50-", "-1235.50"),
        ("+1000", "1000.00"),
        ("1.234.567,89", "1234567.89"),
        ("1.235", "1235.00"),  # único separador + 3 dígitos = milhar
        ("0,5", "0.50"),
        ("R$ 1.000,00", "1000.00"),
    ],
)
def test_parse_money(text: str, expected: str) -> None:
    assert parse_money(text) == Decimal(expected)


def test_parse_money_explicit_decimal_separator() -> None:
    assert parse_money("1.235", ",") == Decimal("1235.00")
    assert parse_money("1.235", ".") == Decimal("1.24")  # arredondamento ROUND_HALF_UP: 1.235 -> 1.24


@pytest.mark.parametrize("text", ["", "abc", "1.2.3,4,5x", "--5"])
def test_parse_money_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_money(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("05/01/2026", datetime(2026, 1, 5)),
        ("05/01/26", datetime(2026, 1, 5)),
        ("2026-01-05", datetime(2026, 1, 5)),
        ("2026-01-05T10:30:00", datetime(2026, 1, 5)),
        ("05/01/2026 10:30", datetime(2026, 1, 5)),
        ("05-01-2026", datetime(2026, 1, 5)),
        ("05.01.2026", datetime(2026, 1, 5)),
        ("5 jan 2026", datetime(2026, 1, 5)),
        ("05 de fevereiro de 2026", datetime(2026, 2, 5)),
        ("12/03/2026", datetime(2026, 3, 12)),  # dia primeiro: nunca 3 de dezembro
    ],
)
def test_parse_date(text: str, expected: datetime) -> None:
    assert parse_date(text) == expected


def test_parse_date_invalid_and_custom_format() -> None:
    with pytest.raises(ValueError):
        parse_date("31/02/2026")
    with pytest.raises(ValueError):
        parse_date("ontem")
    assert parse_date("01/05/2026", "%m/%d/%Y") == datetime(2026, 1, 5)


# ---------------------------------------------------------------------------
# Parser OFX
# ---------------------------------------------------------------------------
def test_parse_ofx_bank() -> None:
    result = parse_ofx(OFX_BANK)
    by_id = {t.external_id: t for t in result.transactions if t.external_id != "A4"}

    a1 = by_id["A1"]
    assert a1.amount == Decimal("-1235.50") and a1.type == "DEBIT"
    assert a1.date == datetime(2026, 1, 5, 23, 30), "o fuso não pode empurrar o lançamento para outro dia"
    assert a1.description == "PADARIA PÃO QUENTE", "acentos em CP1252 devem sobreviver"

    a2 = by_id["A2"]
    assert a2.amount == Decimal("5000.00") and a2.type == "CREDIT"
    assert a2.description == "SALÁRIO EMPRESA" and a2.description_raw == "SALÁRIO EMPRESA | SALARIO"

    a3 = by_id["A3"]  # TRNTYPE=DEBIT com valor positivo: confiamos no TRNTYPE
    assert a3.amount == Decimal("-35.90") and a3.type == "DEBIT"
    assert any("sinal contrário" in w for w in result.warnings)

    assert result.skipped == 1 and "zero" in result.issues[0].reason
    assert result.detected["ofx_account_id"] == "12345-6"


def test_parse_ofx_credit_card_xml() -> None:
    result = parse_ofx(OFX_CARD_XML)
    assert [(t.external_id, t.amount, t.type) for t in result.transactions] == [
        ("C1", Decimal("-89.90"), "DEBIT"),
        ("C2", Decimal("500.00"), "CREDIT"),
    ]


def test_parse_ofx_rejects_garbage_and_multiple_accounts() -> None:
    with pytest.raises(ImportFileError):
        parse_ofx(b"isto nao e ofx")
    two = OFX_BANK.replace(b"</STMTRS>", b"</STMTRS><STMTRS><CURDEF>BRL<BANKACCTFROM><BANKID>1<ACCTID>999<ACCTTYPE>CHECKING</BANKACCTFROM><BANKTRANLIST></BANKTRANLIST></STMTRS>")
    with pytest.raises(ImportFileError, match="contas"):
        parse_ofx(two)


# ---------------------------------------------------------------------------
# Parser CSV
# ---------------------------------------------------------------------------
def test_csv_nubank_bank_uses_identifier_column() -> None:
    result = parse_csv(CSV_NUBANK_BANK, CsvOptions(), account_type="BANK")
    assert result.detected["delimiter"] == "," and result.detected["id_column"] == "Identificador"
    assert [t.external_id for t in result.transactions] == ["aaaa-1", "aaaa-2", "aaaa-3", "aaaa-4"]
    assert result.transactions[0].amount == Decimal("-35.90")
    assert result.transactions[1].type == "CREDIT"


def test_csv_nubank_card_inverts_sign_for_credit_accounts() -> None:
    card = parse_csv(CSV_NUBANK_CARD, CsvOptions(), account_type="CREDIT")
    assert [(t.amount, t.type) for t in card.transactions] == [
        (Decimal("-35.90"), "DEBIT"),
        (Decimal("-35.90"), "DEBIT"),
        (Decimal("1000.00"), "CREDIT"),
    ]
    assert card.transactions[0].category == "restaurante"
    assert any("cartão" in w.lower() for w in card.warnings)

    as_is = parse_csv(CSV_NUBANK_CARD, CsvOptions(sign="as_is"), account_type="CREDIT")
    assert as_is.transactions[0].type == "CREDIT"


def test_csv_itau_semicolon_decimal_comma_and_balance_rows() -> None:
    result = parse_csv(CSV_ITAU, CsvOptions(), account_type="BANK")
    assert [(t.description, t.amount) for t in result.transactions] == [
        ("PIX QRS PADARIA", Decimal("-1235.50")),
        ("SISPAG SALARIO", Decimal("5000.00")),
    ]
    assert result.skipped == 0 and any("saldo" in w for w in result.warnings)


def test_csv_inter_preamble_and_currency_prefix() -> None:
    result = parse_csv(CSV_INTER, CsvOptions(), account_type="BANK")
    assert result.detected["header_line"] == 5
    assert result.detected["description_column"] == "Descrição"
    assert [t.amount for t in result.transactions] == [Decimal("-1235.50"), Decimal("25.00"), Decimal("-25.10")]
    assert result.transactions[0].description == "PIX ENVIADO: PADARIA, LTDA"


def test_csv_c6_separate_debit_and_credit_columns() -> None:
    result = parse_csv(CSV_C6, CsvOptions(), account_type="BANK")
    assert [(t.description, t.amount, t.type) for t in result.transactions] == [
        ("Pix enviado", Decimal("-1235.50"), "DEBIT"),
        ("Salário", Decimal("5000.00"), "CREDIT"),
    ]


def test_csv_explicit_mapping_overrides_detection() -> None:
    raw = b"Quando|Quanto|O que\n05/01/2026|-10,00|Teste\n"
    with pytest.raises(ImportFileError, match="cabeçalho"):
        parse_csv(raw, CsvOptions(), account_type="BANK")
    options = CsvOptions(date_column="Quando", amount_column="Quanto", description_column="O que")
    result = parse_csv(raw, options, account_type="BANK")
    assert result.transactions[0].description == "Teste" and result.transactions[0].amount == Decimal("-10.00")


def test_csv_reports_bad_rows_without_failing() -> None:
    raw = "Data;Valor;Descrição\n05/01/2026;-10,00;Ok\nxx/yy/zzzz;-5,00;Data ruim\n06/01/2026;abc;Valor ruim\n07/01/2026;0,00;Zero\n".encode()
    result = parse_csv(raw, CsvOptions(), account_type="BANK")
    assert len(result.transactions) == 1 and result.skipped == 3
    assert [issue.line for issue in result.issues] == [3, 4, 5]


# ---------------------------------------------------------------------------
# Persistência e idempotência
# ---------------------------------------------------------------------------
async def test_ofx_import_is_idempotent(session: AsyncSession, bank_id: str) -> None:
    first = await svc.import_ofx(session, bank_id, OFX_BANK, filename="jan.ofx")
    assert (first.inserted, first.duplicates, first.duplicates_in_file, first.skipped) == (5, 0, 1, 1)
    assert first.period_start == datetime(2026, 1, 5, 23, 30) and first.period_end == datetime(2026, 1, 16)
    assert first.inserted_income == 5000.0 and first.inserted_expense == pytest.approx(1235.50 + 35.90 + 100 + 50)
    assert any("identificador com dados diferentes" in w for w in first.warnings), "FITID A4 reaproveitado"
    assert await count_transactions(session) == 5

    again = await svc.import_ofx(session, bank_id, OFX_BANK, filename="jan.ofx")
    assert (again.inserted, again.duplicates) == (0, 5)
    assert await count_transactions(session) == 5

    stored = (await session.scalars(select(Transaction).order_by(Transaction.date))).all()
    assert all(len(t.id) == 36 and t.id.startswith("imp_") for t in stored)
    assert {t.provider_code for t in stored} == {"import:ofx"}
    assert {t.account_id for t in stored} == {bank_id}
    assert {(t.type, t.amount) for t in stored if t.description == "COMPRA POSITIVA"} == {("DEBIT", Decimal("-35.90"))}


async def test_overlapping_ofx_only_inserts_new_rows(session: AsyncSession, bank_id: str) -> None:
    await svc.import_ofx(session, bank_id, OFX_BANK)
    extra = OFX_BANK.replace(
        b"</BANKTRANLIST>",
        b"<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20260125<TRNAMT>-20.00<FITID>A9<MEMO>NOVO</STMTTRN></BANKTRANLIST>",
    )
    report = await svc.import_ofx(session, bank_id, extra)
    assert (report.inserted, report.duplicates) == (1, 5)


async def test_same_fitid_in_two_accounts_does_not_collide(session: AsyncSession, bank_id: str, card_id: str) -> None:
    await svc.import_ofx(session, bank_id, OFX_CARD_XML)
    report = await svc.import_ofx(session, card_id, OFX_CARD_XML)
    assert report.inserted == 2 and await count_transactions(session) == 4


async def test_csv_identical_rows_are_distinct_but_reimport_is_idempotent(
    session: AsyncSession, card_id: str
) -> None:
    first = await svc.import_csv(session, card_id, CSV_NUBANK_CARD)  # duas compras idênticas no mesmo dia
    assert (first.inserted, first.duplicates) == (3, 0)
    again = await svc.import_csv(session, card_id, CSV_NUBANK_CARD)
    assert (again.inserted, again.duplicates) == (0, 3)
    assert await count_transactions(session) == 3


async def test_csv_hash_key_ignores_case_and_accents(session: AsyncSession, bank_id: str) -> None:
    a = "Data;Valor;Descrição\n05/01/2026;-10,00;Padaria Pão Quente\n".encode()
    b = "Data;Valor;Descrição\n05/01/2026;-10,00;  PADARIA   PAO QUENTE \n".encode()
    await svc.import_csv(session, bank_id, a)
    report = await svc.import_csv(session, bank_id, b)
    assert (report.inserted, report.duplicates) == (0, 1)


async def test_csv_overlapping_export_keeps_counting_order(session: AsyncSession, bank_id: str) -> None:
    jan = "Data;Valor;Descrição\n05/01/2026;-8,00;Café\n05/01/2026;-8,00;Café\n".encode()
    wider = "Data;Valor;Descrição\n04/01/2026;-3,00;Pão\n05/01/2026;-8,00;Café\n05/01/2026;-8,00;Café\n".encode()
    await svc.import_csv(session, bank_id, jan)
    report = await svc.import_csv(session, bank_id, wider)
    assert (report.inserted, report.duplicates) == (1, 2)


async def test_dry_run_does_not_write(session: AsyncSession, bank_id: str) -> None:
    report = await svc.import_csv(session, bank_id, CSV_ITAU, dry_run=True)
    assert report.dry_run and report.inserted == 2 and len(report.sample) == 2
    assert await count_transactions(session) == 0
    real = await svc.import_csv(session, bank_id, CSV_ITAU)
    assert real.inserted == 2 and not real.sample and await count_transactions(session) == 2


async def test_unknown_account_and_empty_result(session: AsyncSession, bank_id: str) -> None:
    with pytest.raises(svc.AccountNotFoundError):
        await svc.import_csv(session, "nao-existe", CSV_ITAU)
    only_bad = "Data;Valor;Descrição\nxx;yy;zz\n".encode()
    with pytest.raises(ImportFileError, match="Nenhum lançamento válido"):
        await svc.import_csv(session, bank_id, only_bad)


async def test_imported_rows_feed_the_quant_engine(session: AsyncSession, bank_id: str) -> None:
    """O objetivo do módulo: o que foi importado entra nas análises sem adaptação."""
    from datetime import date

    from app.services.quant_engine import QuantEngine

    await svc.import_ofx(session, bank_id, OFX_BANK)
    summary = await QuantEngine(session).year_summary(2026, date(2026, 2, 1))
    assert summary.income_total == pytest.approx(5000.0)
    assert summary.expense_total == pytest.approx(1235.50 + 35.90 + 100 + 50)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
async def test_endpoint_ofx(client: httpx.AsyncClient, bank_id: str) -> None:
    files = {"file": ("jan.ofx", OFX_BANK, "application/x-ofx")}
    response = await client.post("/api/v1/import/ofx", files=files, data={"account_id": bank_id})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "ofx" and body["inserted"] == 5 and body["duplicates"] == 0

    again = await client.post("/api/v1/import/ofx", files=files, data={"account_id": bank_id})
    assert again.json()["inserted"] == 0 and again.json()["duplicates"] == 5


async def test_endpoint_csv_with_mapping_and_dry_run(client: httpx.AsyncClient, bank_id: str) -> None:
    raw = b"Quando|Quanto|O que\n05/01/2026|-10,00|Teste\n"
    files = {"file": ("extrato.csv", raw, "text/csv")}
    data = {
        "account_id": bank_id,
        "date_column": "Quando",
        "amount_column": "Quanto",
        "description_column": "O que",
        "decimal_separator": "",  # campo de formulário vazio = detectar
        "dry_run": "true",
    }
    response = await client.post("/api/v1/import/csv", files=files, data=data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dry_run"] and body["inserted"] == 1 and body["sample"][0]["amount"] == -10.0
    assert body["detected"]["delimiter"] == "|"


async def test_endpoint_errors(client: httpx.AsyncClient, bank_id: str) -> None:
    csv_file = {"file": ("x.csv", CSV_ITAU, "text/csv")}
    assert (await client.post("/api/v1/import/csv", files=csv_file, data={"account_id": "nope"})).status_code == 404
    bad_ext = {"file": ("x.pdf", b"%PDF", "application/pdf")}
    assert (await client.post("/api/v1/import/ofx", files=bad_ext, data={"account_id": bank_id})).status_code == 415
    empty = {"file": ("x.csv", b"   ", "text/csv")}
    assert (await client.post("/api/v1/import/csv", files=empty, data={"account_id": bank_id})).status_code == 422
    garbage = {"file": ("x.ofx", b"nada a ver", "application/x-ofx")}
    assert (await client.post("/api/v1/import/ofx", files=garbage, data={"account_id": bank_id})).status_code == 422
    no_header = {"file": ("x.csv", b"a;b;c\n1;2;3\n", "text/csv")}
    response = await client.post("/api/v1/import/csv", files=no_header, data={"account_id": bank_id})
    assert response.status_code == 422 and "cabeçalho" in response.json()["detail"]
    invalid_sign = await client.post(
        "/api/v1/import/csv", files=csv_file, data={"account_id": bank_id, "sign": "talvez"}
    )
    assert invalid_sign.status_code == 422


async def test_endpoint_rejects_oversized_upload(
    client: httpx.AsyncClient, bank_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 100)
    files = {"file": ("big.csv", b"Data;Valor;Descricao\n" + b"x" * 500, "text/csv")}
    response = await client.post("/api/v1/import/csv", files=files, data={"account_id": bank_id})
    assert response.status_code == 413
