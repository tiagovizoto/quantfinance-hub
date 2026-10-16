import { useEffect, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useLoanStrategy } from "../api/hooks";
import type { LoanStrategyResponse, StrategyResult } from "../api/types";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { brl, brlCompactFmt, pct, monthLabel } from "../lib/format";

const LOAN_COLORS = ["#2748c9", "#b07a00", "#7a3e9d", "#1b8ea6", "#566370", "#c0507a"];
const MAX_SLIDER = 5000;

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

const monthsText = (n: number | null) => (n === null ? "mais de 50 anos" : `${n} ${n === 1 ? "mês" : "meses"}`);
const when = (on: string | null) => (on ? monthLabel(on) : "—");

// ---------------------------------------------------------------- controle do valor extra
function ExtraControl({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div className="mb-8 flex flex-wrap items-end gap-x-6 gap-y-3">
      <label className="flex flex-col text-sm">
        <span className="text-muted">Quanto a mais você consegue pagar por mês (R$)</span>
        <input
          type="number"
          inputMode="decimal"
          min={0}
          step={50}
          value={value}
          onChange={(e) => onChange(Math.max(0, Number(e.target.value) || 0))}
          className="num mt-1 w-40 rounded-sm border border-line bg-surface px-2 py-1"
        />
      </label>
      <label className="flex min-w-[220px] flex-1 flex-col text-sm">
        <span className="sr-only">Ajustar valor extra</span>
        <input
          type="range"
          min={0}
          max={MAX_SLIDER}
          step={50}
          value={Math.min(value, MAX_SLIDER)}
          onChange={(e) => onChange(Number(e.target.value))}
          aria-label="Ajustar valor extra por mês"
          className="w-full accent-accent"
        />
        <span className="num mt-1 flex justify-between text-xs text-muted">
          <span>R$ 0</span>
          <span>{brl(MAX_SLIDER)}</span>
        </span>
      </label>
    </div>
  );
}

// ---------------------------------------------------------------- partes
function Strategies({ d }: { d: LoanStrategyResponse }) {
  const best = d.strategies.find((s) => s.key === d.best_strategy);
  const baseline = d.strategies.find((s) => s.key === "baseline");

  const row = (s: StrategyResult) => (
    <tr key={s.key} className="border-b border-line/60 align-top">
      <th scope="row" className="py-2 pr-4 text-left font-normal">
        {s.label}
        {s.key === d.best_strategy && <span className="ml-2 text-xs font-medium text-gain">menos juros</span>}
      </th>
      <td className="py-2 pr-4 text-right">
        {when(s.debt_free_on)}
        <span className="block text-xs text-muted">{monthsText(s.months_to_debt_free)}</span>
      </td>
      <td className="py-2 pr-4 text-right">{brl(s.total_interest)}</td>
      <td className="py-2 pr-4 text-right">{s.key === "baseline" ? "—" : brl(s.interest_saved)}</td>
      <td className="py-2">
        <ol className="space-y-0.5">
          {s.payoff_order.map((p) => (
            <li key={p.loan_id}>
              {p.name} <span className="text-muted">({when(p.on)})</span>
            </li>
          ))}
        </ol>
      </td>
    </tr>
  );

  return (
    <>
      <FigureRow>
        <Figure label="Dívida total" value={brl(d.total_balance)} hint={`${d.loans.length} empréstimos`} />
        <Figure
          label="Parcelas hoje"
          value={brl(d.total_installments_now)}
          hint={
            d.avg_monthly_income
              ? `${pct(d.total_installments_now / d.avg_monthly_income, 0)} da renda média`
              : "por mês (estimado)"
          }
        />
        <Figure
          label="Livre de dívidas hoje"
          value={when(baseline?.debt_free_on ?? null)}
          hint={`${monthsText(baseline?.months_to_debt_free ?? null)}, só pagando as parcelas`}
        />
        <Figure
          label={best ? best.label : "Com estratégia"}
          value={when(best?.debt_free_on ?? null)}
          hint={
            best
              ? `${brl(best.interest_saved)} a menos de juros, ${Math.max(best.months_saved, 0)} meses antes`
              : undefined
          }
        />
      </FigureRow>

      <div className="mt-8 overflow-x-auto">
        <table className="num w-full min-w-[720px] text-sm">
          <caption className="sr-only">Comparação entre as formas de quitar os empréstimos</caption>
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">Estratégia</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Livre de dívidas em</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Juros que vai pagar</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Economia de juros</th>
              <th scope="col" className="py-2 font-medium">Ordem de quitação</th>
            </tr>
          </thead>
          <tbody>{d.strategies.map(row)}</tbody>
        </table>
      </div>
      <p className="mt-3 max-w-[80ch] text-sm text-muted">
        Nas duas estratégias, a parcela de cada empréstimo que termina passa a pagar o próximo da fila
        {d.extra_monthly > 0 ? `, somada aos ${brl(d.extra_monthly)} extras por mês` : ""}. Maior CET primeiro costuma
        render menos juros; menor saldo primeiro zera parcelas mais cedo.
      </p>
    </>
  );
}

function DebtCurve({ d }: { d: LoanStrategyResponse }) {
  const data = d.debt_curve.map((p) => ({
    label: monthLabel(p.on),
    "Só as parcelas atuais": p.baseline,
    "Maior CET primeiro": p.avalanche,
    "Menor saldo primeiro": p.snowball,
  }));
  return (
    <div className="mt-10">
      <h3 className="text-xl">Quanto você ainda deve, mês a mês</h3>
      <div className="mt-3 h-72" role="img" aria-label="Saldo devedor total ao longo do tempo em cada estratégia">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="label" interval="preserveStartEnd" minTickGap={48} tick={{ fill: "var(--color-muted)", fontSize: 12 }} tickLine={false} />
            <YAxis width={72} tickFormatter={brlCompactFmt} tick={{ fill: "var(--color-muted)", fontSize: 12 }} tickLine={false} axisLine={false} />
            <Tooltip formatter={(value) => brl(Number(value))} />
            <Legend iconType="plainline" />
            <Line dataKey="Só as parcelas atuais" stroke="var(--color-muted)" strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line dataKey="Maior CET primeiro" stroke="var(--color-accent)" strokeWidth={2.5} dot={false} isAnimationActive={false} />
            <Line dataKey="Menor saldo primeiro" stroke="#b07a00" strokeWidth={2.5} dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Timeline({ d }: { d: LoanStrategyResponse }) {
  const hasIncome = d.timeline.some((p) => p.commitment !== null);
  const data = d.timeline.map((p) => ({
    label: monthLabel(p.on),
    ...Object.fromEntries(p.by_loan.map((v, i) => [`l${i}`, v])),
    commitment: p.commitment,
  }));
  return (
    <div className="mt-10">
      <h3 className="text-xl">Parcelas por mês, mantido o ritmo atual</h3>
      <p className="mt-1 max-w-[70ch] text-sm text-muted">
        Cada vez que um empréstimo termina, a parcela dele sai do orçamento
        {hasIncome ? " e a linha mostra quanto da renda média ainda fica comprometida" : ""}.
      </p>
      <div className="mt-3 h-72" role="img" aria-label="Parcelas mensais por empréstimo e comprometimento da renda">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: hasIncome ? 8 : 16, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="label" interval="preserveStartEnd" minTickGap={48} tick={{ fill: "var(--color-muted)", fontSize: 12 }} tickLine={false} />
            <YAxis yAxisId="money" width={64} tickFormatter={brlCompactFmt} tick={{ fill: "var(--color-muted)", fontSize: 12 }} tickLine={false} axisLine={false} />
            {hasIncome && (
              <YAxis
                yAxisId="share"
                orientation="right"
                width={44}
                tickFormatter={(v) => `${Math.round(Number(v) * 100)}%`}
                tick={{ fill: "var(--color-muted)", fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
            )}
            <Tooltip formatter={(value, name) => (name === "% da renda" ? pct(Number(value), 1) : brl(Number(value)))} />
            <Legend iconType="square" />
            {d.loans.map((loan, i) => (
              <Bar
                key={loan.loan_id}
                yAxisId="money"
                stackId="parcelas"
                dataKey={`l${i}`}
                name={loan.name}
                fill={LOAN_COLORS[i % LOAN_COLORS.length]}
                isAnimationActive={false}
              />
            ))}
            {hasIncome && (
              <Line yAxisId="share" dataKey="commitment" name="% da renda" stroke="var(--color-ink)" strokeWidth={2} dot={false} isAnimationActive={false} />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function LoansTable({ d }: { d: LoanStrategyResponse }) {
  return (
    <div className="mt-10">
      <h3 className="text-xl">Quanto cada empréstimo custa</h3>
      <div className="mt-3 overflow-x-auto">
        <table className="num w-full min-w-[820px] text-sm">
          <caption className="sr-only">Saldo, taxa, parcela e juros restantes de cada empréstimo</caption>
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">Empréstimo</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Saldo devedor</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">CET (a.a.)</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Parcelas restantes</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Parcela hoje</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Juros que ainda vai pagar</th>
              <th scope="col" className="py-2 text-right font-medium">Termina em</th>
            </tr>
          </thead>
          <tbody>
            {d.loans.map((l, i) => (
              <tr key={l.loan_id} className="border-b border-line/60">
                <th scope="row" className="py-2 pr-4 text-left font-normal">
                  <span className="mr-2 inline-block h-2.5 w-2.5 align-baseline" style={{ background: LOAN_COLORS[i % LOAN_COLORS.length] }} aria-hidden />
                  {l.name}
                  <span className="ml-2 text-xs text-muted">{l.amortization}</span>
                </th>
                <td className="py-2 pr-4 text-right">{brl(l.balance)}</td>
                <td className="py-2 pr-4 text-right">{l.cet_annual === null ? "—" : `${l.cet_annual.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%`}</td>
                <td className="py-2 pr-4 text-right">{l.remaining_installments}</td>
                <td className="py-2 pr-4 text-right">{brl(l.installment_first)}</td>
                <td className="py-2 pr-4 text-right">
                  {brl(l.interest_remaining)}
                  <span className="block text-xs text-muted">{pct(l.interest_remaining / l.balance, 0)} do saldo</span>
                </td>
                <td className="py-2 text-right">
                  {when(l.ends_on)}
                  {l.installment_last > 0 && <span className="block text-xs text-muted">libera {brl(l.installment_last)}/mês</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Body({ d }: { d: LoanStrategyResponse }) {
  if (d.loans.length === 0) {
    return (
      <>
        <p className="text-muted">Nenhum empréstimo ativo com saldo e parcelas restantes conhecidos.</p>
        <Notes items={d.warnings} />
      </>
    );
  }
  return (
    <>
      <Strategies d={d} />
      <DebtCurve d={d} />
      <Timeline d={d} />
      <LoansTable d={d} />
      <Notes items={d.warnings} />
    </>
  );
}

export function LoanStrategySection() {
  const [extra, setExtra] = useState(0);
  const query = useLoanStrategy(useDebounced(extra, 300));
  return (
    <Section
      id="quitacao"
      title="Plano de quitação"
      intro="Em que ordem pagar os empréstimos e quanto o valor extra de cada mês encurta o caminho."
    >
      <ExtraControl value={extra} onChange={setExtra} />
      <div aria-busy={query.isFetching} className={query.isPlaceholderData ? "opacity-60" : undefined}>
        <Async query={query}>{(d) => <Body d={d} />}</Async>
      </div>
    </Section>
  );
}
