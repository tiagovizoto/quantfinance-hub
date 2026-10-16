# QuantFinance Hub

Gestão financeira pessoal + análise quantitativa sobre dados do Open Finance (Pluggy / MeuPluggy).

## Estrutura

```
quantfinance-hub/
├── backend/
│   ├── alembic/                 # migrações (async, modo batch p/ SQLite)
│   │   └── versions/0001_schema_inicial.py
│   ├── alembic.ini
│   ├── app/
│   │   ├── main.py              # FastAPI
│   │   ├── database.py          # engine async + PRAGMA foreign_keys=ON
│   │   ├── models.py            # Item, Account, Transaction, Bill, Investment, Loan
│   │   ├── core/config.py       # settings (.env)
│   │   ├── api/routers/sync.py  # POST /api/v1/sync, POST /sync/{item_id}, GET /sync/status
│   │   ├── schemas/sync.py
│   │   └── services/
│   │       ├── pluggy_service.py  # cliente Pluggy + upsert
│   │       ├── importer_service.py # importação de extratos OFX/CSV (idempotente)
│   │       └── quant_engine.py    # métricas quantitativas
│   ├── tests/                   # pytest (importador OFX/CSV)
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env.example
└── frontend/                    # React + Vite + TS + Tailwind v4 + Recharts
    ├── src/api/                 # client, hooks (React Query) e tipos espelhando os schemas
    ├── src/sections/            # Para onde foi (ano), Fluxo de caixa, Gastos, Projeção, Dívida, Quitação, Risco
    ├── src/components/ui.tsx    # Section, Figure, Meter, estados de carga/erro
    └── vite.config.ts           # proxy /api -> http://localhost:8000
```

## Rodando o backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # preencha PLUGGY_CLIENT_ID/SECRET/ITEM_ID
alembic upgrade head
uvicorn app.main:app --reload                         # API em http://localhost:8000/docs

# sincronização via CLI (incremental; --full reimporta todo o histórico)
python -m app.services.pluggy_service            # usa PLUGGY_ITEM_ID
python -m app.services.pluggy_service <itemId> --full
```

## Rodando o frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (o Vite encaminha /api para o backend em :8000)
npm run build      # checagem de tipos + build de produção
```

Suba o backend antes. O botão "Sincronizar agora" usa o `PLUGGY_ITEM_ID` do `.env`.

## Endpoints quantitativos (`/api/v1/quant`)

| Rota | O que devolve |
|---|---|
| `GET /year-summary?year=2026` | para onde foi o dinheiro no ano: totais, meses e categorias x mês (aceita qualquer ano com dados) |
| `GET /loan-strategy?extra=500` | plano de quitação: avalanche (maior CET) x bola de neve (menor saldo) com valor extra mensal, linha do tempo das parcelas e dívida mês a mês |
| `GET /cashflow` | taxa de poupança, burn rate (diário/mensal), runway normal e estresse (+2σ) |
| `GET /anomalies` | Z-score do gasto do mês por categoria vs. últimos 6 meses |
| `GET /monte-carlo` | patrimônio líquido em 12/24/60 meses (P10/P50/P90), 10.000 simulações |
| `GET /arbitrage` | amortizar dívida vs. manter investido (VPL, IR regressivo) |
| `GET /credit-risk` | utilização de limite, cobertura da dívida, comprometimento de renda |

Todas aceitam `as_of=YYYY-MM-DD`. Monte Carlo e arbitragem aceitam premissas de mercado por query string
(`cdi_annual`, `ipca_annual`, `equity_return_annual`, ...; taxas em fração, ex.: `0.1365`).
Documentação interativa em `/docs`.

## Importação de extratos (`/api/v1/import`)

Para contas ou cartões que ficaram fora da Pluggy. As linhas entram na mesma tabela `transactions`
(`provider_code` = `import:ofx` ou `import:csv`) e aparecem em todas as análises.

| Rota | Campos do formulário (`multipart/form-data`) |
|---|---|
| `POST /import/ofx` | `file` (.ofx/.qfx, uma conta por arquivo), `account_id`, `dry_run` |
| `POST /import/csv` | `file` (.csv), `account_id`, `dry_run` e, só se a detecção errar: `date_column`, `description_column`, `amount_column`, `debit_column`, `credit_column`, `id_column`, `category_column`, `delimiter`, `decimal_separator`, `date_format`, `sign` |

```bash
# o account_id é o id da conta em `accounts` (veja GET em /docs ou o SQLite)
curl -F "file=@extrato.ofx" -F account_id=SUA_CONTA http://localhost:8000/api/v1/import/ofx
curl -F "file=@fatura.csv" -F account_id=SEU_CARTAO -F dry_run=true http://localhost:8000/api/v1/import/csv
```

- **Sem duplicar:** reenviar o mesmo arquivo (ou um que se sobreponha) insere só o que é novo. OFX usa `conta + FITID`; CSV
  usa o identificador do banco se houver (Nubank) ou `data + valor + descrição` (sem acento/caixa), contando repetições
  dentro do arquivo para não fundir duas compras iguais no mesmo dia.
- **Sinal:** conta corrente = negativo é saída. Em **cartão**, o padrão é positivo = compra; se o seu CSV for o contrário,
  envie `sign=as_is`. Use `dry_run=true` na primeira vez para conferir o que foi detectado.
- Não importe, para a mesma conta, um extrato de um período que a Pluggy já sincroniza: os ids são diferentes e
  as linhas apareceriam duas vezes.
- Testes: `pip install -r requirements-dev.txt && pytest` (dentro de `backend/`).

## Migrar para PostgreSQL

Instale `asyncpg`, altere `DATABASE_URL=postgresql+asyncpg://user:pass@host/db` e rode `alembic upgrade head`.
