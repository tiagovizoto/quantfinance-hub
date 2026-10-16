# QuantFinance Hub

Gestão financeira pessoal + análise quantitativa sobre dados do Open Finance (Pluggy / MeuPluggy).

> As imagens abaixo usam **dados fictícios** de demonstração.

![Visão geral](docs/screenshots/01-visao-geral.png)

## Telas

### Explorar os gastos
Do período à categoria, ao estabelecimento e ao dia.

![Explorar os gastos](docs/screenshots/03-explorar.png)

### A rede do dinheiro
Grafo interativo: receita → conta → categoria → estabelecimento. Passe o mouse para ver o caminho do dinheiro; clique para fixar; arraste para reorganizar.

![Rede do dinheiro](docs/screenshots/04-rede-do-dinheiro.png)

### Da renda ao que sobrou, e concentração
![Cascata da renda](docs/screenshots/05-cascata-renda.png)
![Pareto](docs/screenshots/06-pareto.png)

### Para onde foi o dinheiro no ano
![Para onde foi](docs/screenshots/02-para-onde-foi.png)

### Caixa: os próximos dias
Saldo previsto com faixa de incerteza, ritmo do mês e parcelados no cartão.

![Caixa](docs/screenshots/07-caixa.png)

### Carteira e patrimônio
![Carteira](docs/screenshots/08-carteira.png)

### Plano de quitação
![Cascata de dívidas](docs/screenshots/09-cascata-dividas.png)
![Juros x principal](docs/screenshots/10-juros-x-principal.png)

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
    ├── src/sections/            # Para onde foi (ano), Explorar, Caixa, Fluxo de caixa, Gastos, Projeção, Carteira, Dívida, Quitação, Risco
    ├── src/components/ui.tsx    # Section, Figure, Meter, estados de carga/erro
    └── vite.config.ts           # proxy /api -> http://localhost:8000
```

## Rodando o backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # preencha PLUGGY_CLIENT_ID/SECRET/ITEM_ID (vários itens: separados por vírgula)
alembic upgrade head
uvicorn app.main:app --reload                         # API em http://localhost:8000/docs
# alternativas equivalentes: python main.py  |  python app/main.py  |  python -m app.main

# sincronização via CLI (incremental; --full reimporta todo o histórico)
python -m app.services.pluggy_service            # sincroniza todos os itens de PLUGGY_ITEM_ID
python -m app.services.pluggy_service <itemId> [<itemId> ...] --full
```

## Rodando o frontend

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (o Vite encaminha /api para o backend em :8000)
npm run build      # checagem de tipos + build de produção
```

Suba o backend antes. O botão "Sincronizar agora" usa o `PLUGGY_ITEM_ID` do `.env`.

### Vários bancos (MeuPluggy)
No Pluggy Connect, ao escolher o MeuPluggy você ainda seleciona o banco, e **cada banco conectado gera um `itemId` próprio**. Repita o Connect para cada banco e coloque todos no `.env`, separados por vírgula:

```
PLUGGY_ITEM_ID=id-nubank,id-itau,id-inter
```

`POST /api/v1/sync` e o botão "Sincronizar agora" sincronizam todos em sequência. Se um banco falhar (conexão expirada, por exemplo), os outros continuam e a resposta lista os que falharam em `failed`. Para sincronizar um só: `POST /api/v1/sync/{itemId}`. Os `itemId` aparecem no painel da Pluggy (Items) e no retorno do evento `onSuccess` do Connect.

## Endpoints quantitativos (`/api/v1/quant`)

| Rota | O que devolve |
|---|---|
| `GET /year-summary?year=2026` | para onde foi o dinheiro no ano: totais, meses e categorias x mês (aceita qualquer ano com dados) |
| `GET /spending?year=2026&month=9` | explorador de gastos: categorias (com comerciantes e maiores lançamentos), calendário diário, dia da semana, gastos recorrentes e maiores gastos avulsos; `month` vazio = ano todo |
| `GET /cash-forecast?days=60` | saldo diário previsto (fixos, salário, parcelas, fatura, gasto variável) com faixa p10–p90 e primeiro dia negativo |
| `GET /pace` | gasto acumulado do mês contra o mês passado e a média; fechamento projetado |
| `GET /category-change?year=&month=&mode=` | variação por categoria (`previous_month`, `previous_year`, `average_6m`) |
| `GET /structure?year=` | gasto mensal em fixo / variável / avulso |
| `GET /concentration?year=&month=` | Pareto de categorias e estabelecimentos (quantos explicam 50% e 80%) |
| `GET /portfolio` | composição, vencimentos, retorno x CDI por posição |
| `GET /installments` | parcelas futuras de compras já feitas no cartão |
| `GET /money-graph?year=&month=` | nós e ligações receita → conta → categoria → estabelecimento (grafo) |
| `GET /net-worth` | patrimônio atual e histórico diário |

O histórico de patrimônio é gravado (tabela `net_worth_snapshots`, migração `0002`) a cada sincronização; ele só existe a partir do primeiro sync depois de `alembic upgrade head`. Não há orçamento por categoria. A projeção de caixa e a detecção de gastos fixos são estimativas por padrão histórico.
| `GET /loan-strategy?extra=500` | plano de quitação: avalanche (maior CET) x bola de neve (menor saldo) com valor extra mensal, linha do tempo das parcelas, dívida mês a mês e juros x principal de cada pagamento |
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
