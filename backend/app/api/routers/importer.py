"""Rotas de importação de extratos: ``POST /api/v1/import/ofx`` e ``/import/csv``."""

from pathlib import PurePath
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.importer import CsvOptions, ImportReport
from app.services import importer_service
from app.services.importer_service import AccountNotFoundError, ImportFileError

router = APIRouter(prefix="/import", tags=["import"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AccountIdForm = Annotated[str, Form(description="ID da conta/cartão de destino em `accounts`.")]
DryRunForm = Annotated[
    bool, Form(description="Só simula: lê o arquivo e mostra o que seria inserido, sem gravar.")
]


async def _read_upload(file: UploadFile, allowed_suffixes: tuple[str, ...]) -> bytes:
    """Lê o upload respeitando o limite de tamanho, sem carregar um arquivo gigante inteiro."""
    suffix = PurePath(file.filename or "").suffix.lower()
    if suffix not in allowed_suffixes:
        raise HTTPException(
            status_code=415,
            detail=f"Extensão '{suffix or '(nenhuma)'}' não suportada; envie {' ou '.join(allowed_suffixes)}.",
        )
    content = await file.read(importer_service.MAX_UPLOAD_BYTES + 1)
    if len(content) > importer_service.MAX_UPLOAD_BYTES:
        limit_mb = importer_service.MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"Arquivo maior que {limit_mb} MB.")
    if not content.strip():
        raise HTTPException(status_code=422, detail="O arquivo está vazio.")
    return content


@router.post(
    "/ofx",
    response_model=ImportReport,
    summary="Importa um extrato OFX",
    responses={404: {"description": "Conta não encontrada"}, 422: {"description": "OFX inválido"}},
)
async def import_ofx(
    session: SessionDep,
    file: Annotated[UploadFile, File(description="Arquivo .ofx de uma única conta ou cartão.")],
    account_id: AccountIdForm,
    dry_run: DryRunForm = False,
) -> ImportReport:
    content = await _read_upload(file, (".ofx", ".qfx"))
    try:
        return await importer_service.import_ofx(
            session, account_id, content, filename=file.filename, dry_run=dry_run
        )
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def csv_options_form(
    date_column: Annotated[str | None, Form(description="Coluna da data.")] = None,
    description_column: Annotated[str | None, Form(description="Coluna da descrição.")] = None,
    amount_column: Annotated[str | None, Form(description="Coluna do valor (com sinal).")] = None,
    debit_column: Annotated[str | None, Form(description="Coluna de saídas.")] = None,
    credit_column: Annotated[str | None, Form(description="Coluna de entradas.")] = None,
    id_column: Annotated[str | None, Form(description="Coluna de identificador único.")] = None,
    category_column: Annotated[str | None, Form(description="Coluna de categoria.")] = None,
    delimiter: Annotated[str | None, Form(description="Separador; vazio = detectar.")] = None,
    decimal_separator: Annotated[str | None, Form(description="',' ou '.'; vazio = detectar.")] = None,
    date_format: Annotated[str | None, Form(description="Formato strptime, ex.: %d/%m/%Y.")] = None,
    sign: Annotated[str, Form(description="auto | as_is | invert")] = "auto",
) -> CsvOptions:
    """Campos de formulário -> ``CsvOptions`` (uma dependência explícita, pois um modelo
    Pydantic como parâmetro de formulário não funciona junto de ``UploadFile``)."""
    try:
        return CsvOptions(
            date_column=date_column,
            description_column=description_column,
            amount_column=amount_column,
            debit_column=debit_column,
            credit_column=credit_column,
            id_column=id_column,
            category_column=category_column,
            delimiter=delimiter,
            decimal_separator=decimal_separator,  # type: ignore[arg-type]
            date_format=date_format,
            sign=sign,  # type: ignore[arg-type]
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False)) from exc


@router.post(
    "/csv",
    response_model=ImportReport,
    summary="Importa um extrato CSV (Nubank, Itaú, Inter, C6...)",
    responses={404: {"description": "Conta não encontrada"}, 422: {"description": "CSV inválido"}},
)
async def import_csv(
    session: SessionDep,
    file: Annotated[UploadFile, File(description="Arquivo .csv do extrato ou da fatura.")],
    account_id: AccountIdForm,
    options: Annotated[CsvOptions, Depends(csv_options_form)],
    dry_run: DryRunForm = False,
) -> ImportReport:
    content = await _read_upload(file, (".csv", ".txt"))
    try:
        return await importer_service.import_csv(
            session, account_id, content, options, filename=file.filename, dry_run=dry_run
        )
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ImportFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
