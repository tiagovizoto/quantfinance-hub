import { useState } from "react";

import { useCashflow, useCreditRisk, useMonteCarlo, useSync, useSyncStatus } from "./api/hooks";
import type { Assumptions } from "./api/types";
import { AssumptionsPanel } from "./components/AssumptionsPanel";
import { Figure, FigureRow } from "./components/ui";
import { brl, monthsText, pct, relativeTime } from "./lib/format";
import { AnomaliesSection } from "./sections/AnomaliesSection";
import { ArbitrageSection } from "./sections/ArbitrageSection";
import { CashflowSection } from "./sections/CashflowSection";
import { CreditRiskSection } from "./sections/CreditRiskSection";
import { LoanStrategySection } from "./sections/LoanStrategySection";
import { MonteCarloSection } from "./sections/MonteCarloSection";
import { YearSection } from "./sections/YearSection";

const NAV = [
  { href: "#ano", label: "Para onde foi" },
  { href: "#fluxo", label: "Fluxo de caixa" },
  { href: "#gastos", label: "Gastos" },
  { href: "#projecao", label: "Projeção" },
  { href: "#divida", label: "Dívida" },
  { href: "#quitacao", label: "Quitação" },
  { href: "#risco", label: "Risco" },
];

function Header() {
  const status = useSyncStatus();
  const sync = useSync();
  const last = status.data?.items[0]?.last_synced_at ?? null;

  return (
    <header className="sticky top-0 z-10 border-b border-line bg-paper/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1120px] flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3 sm:px-8">
        <p className="font-serif text-xl">QuantFinance Hub</p>
        <nav aria-label="Seções" className="order-3 -mx-1 flex w-full gap-1 overflow-x-auto text-sm sm:order-none sm:w-auto sm:flex-1">
          {NAV.map((item) => (
            <a key={item.href} href={item.href} className="whitespace-nowrap rounded-sm px-2 py-1 text-muted hover:text-ink">
              {item.label}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          <span className="hidden text-muted sm:inline" aria-live="polite">
            {sync.isPending ? "Sincronizando…" : `Sincronizado ${relativeTime(last)}`}
          </span>
          <button
            type="button"
            onClick={() => sync.mutate()}
            disabled={sync.isPending}
            className="rounded-sm bg-ink px-3 py-1.5 text-surface hover:bg-accent disabled:opacity-60"
          >
            Sincronizar agora
          </button>
        </div>
      </div>
      {(sync.isError || sync.isSuccess) && (
        <div className="border-t border-line bg-surface" role="status">
          <p className="mx-auto max-w-[1120px] px-4 py-2 text-sm sm:px-8">
            {sync.isError ? (
              <span className="text-loss">Falha na sincronização: {sync.error.message}</span>
            ) : (
              <>
                Importados {sync.data.accounts} contas, {sync.data.transactions} transações, {sync.data.investments}{" "}
                investimentos e {sync.data.loans} empréstimos.
                {sync.data.warnings.length > 0 && <span className="text-warn"> {sync.data.warnings.join(" ")}</span>}
              </>
            )}
          </p>
        </div>
      )}
    </header>
  );
}

function Hero({ assumptions }: { assumptions: Assumptions }) {
  const cashflow = useCashflow();
  const projection = useMonteCarlo(assumptions);
  const credit = useCreditRisk();

  const c = cashflow.data;
  const p24 = projection.data?.horizons.find((h) => h.months === 24);

  let headline = "Carregando seus números…";
  if (c) {
    const rate = c.savings_rate_window;
    if (rate === null) {
      headline = "Ainda não há meses fechados suficientes para calcular quanto você poupa.";
    } else if (rate >= 0) {
      headline = `Você poupou ${pct(rate, 0)} da renda nos últimos ${c.months_used.length} meses, e sua liquidez cobre ${monthsText(c.runway_months)} de gastos.`;
    } else {
      headline = `Nos últimos ${c.months_used.length} meses você gastou ${pct(-rate, 0)} a mais do que recebeu; a liquidez cobre ${monthsText(c.runway_months)} de gastos.`;
    }
  } else if (cashflow.isError) {
    headline = "Não foi possível montar o resumo.";
  }

  return (
    <div className="pt-10 pb-8">
      <h1 className="max-w-[24ch] text-[2.4rem] leading-[1.12] sm:text-5xl">{headline}</h1>
      {p24 && (
        <p className="num mt-4 max-w-[62ch] text-lg text-muted">
          Mantido o ritmo, o patrimônio mediano em 24 meses fica perto de {brl(p24.p50)}, numa faixa de {brl(p24.p10)} a {brl(p24.p90)}.
        </p>
      )}
      <div className="mt-8">
        <FigureRow>
          <Figure label="Patrimônio líquido" value={brl(projection.data?.inputs.initial_net_worth)} />
          <Figure label="Liquidez imediata" value={brl(c?.immediate_liquidity)} />
          <Figure label="Dívida total" value={brl(credit.data?.total_debt)} hint="financiamentos e cartões" />
          <Figure label="Uso do limite" value={pct(credit.data?.utilization_total, 0)} hint="dos cartões" />
        </FigureRow>
      </div>
    </div>
  );
}

export default function App() {
  const [assumptions, setAssumptions] = useState<Assumptions>({});

  return (
    <>
      <a href="#ano" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:bg-surface focus:p-2">
        Ir para o conteúdo
      </a>
      <Header />
      <main className="mx-auto max-w-[1120px] px-4 sm:px-8">
        <Hero assumptions={assumptions} />
        <div className="mb-10">
          <AssumptionsPanel value={assumptions} onApply={setAssumptions} />
        </div>
        <YearSection />
        <CashflowSection />
        <AnomaliesSection />
        <MonteCarloSection assumptions={assumptions} />
        <ArbitrageSection assumptions={assumptions} />
        <LoanStrategySection />
        <CreditRiskSection />
      </main>
      <footer className="mx-auto max-w-[1120px] px-4 pb-10 text-xs text-muted sm:px-8">
        Ferramenta pessoal de análise. Projeções dependem de premissas e não são recomendação de investimento.
      </footer>
    </>
  );
}
