import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCashflow } from "../api/hooks";
import type { CashflowResponse } from "../api/types";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { brl, brlCompactFmt, decimal, monthLabel, monthsText, pct } from "../lib/format";

function Body({ d }: { d: CashflowResponse }) {
  const current = d.current_month_to_date;
  const data = d.monthly.map((m) => ({
    label: monthLabel(m.month) + (m.month === current.month ? " (parcial)" : ""),
    Receitas: m.income,
    Despesas: m.expense,
    Poupança: m.savings,
  }));

  return (
    <>
      <FigureRow>
        <Figure
          label="Taxa de poupança"
          value={pct(d.savings_rate_window)}
          hint={`média dos últimos ${d.months_used.length} meses`}
        />
        <Figure
          label="Gasto mensal médio"
          value={brl(d.burn_rate_monthly)}
          hint={`${brl(d.burn_rate_daily)} por dia`}
        />
        <Figure label="Liquidez imediata" value={brl(d.immediate_liquidity)} hint="saldo das contas correntes" />
        <Figure
          label="Quanto tempo dura"
          value={monthsText(d.runway_months)}
          hint={`${monthsText(d.runway_stress_months)} se os gastos subirem 2σ`}
        />
      </FigureRow>

      <div className="mt-8 h-72" role="img" aria-label="Gráfico de receitas, despesas e poupança por mês">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: "var(--color-muted)", fontSize: 12 }} tickLine={false} />
            <YAxis
              width={64}
              tickFormatter={brlCompactFmt}
              tick={{ fill: "var(--color-muted)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip formatter={(value) => brl(Number(value))} />
            <Legend iconType="plainline" />
            <Bar dataKey="Receitas" fill="var(--color-gain)" isAnimationActive={false} maxBarSize={28} />
            <Bar dataKey="Despesas" fill="var(--color-loss)" isAnimationActive={false} maxBarSize={28} />
            <Line
              dataKey="Poupança"
              stroke="var(--color-accent)"
              strokeWidth={2}
              dot={{ r: 3 }}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <p className="num mt-3 text-sm text-muted">
        Mês corrente até agora: receitas {brl(current.income)}, despesas {brl(current.expense)}
        {current.savings_rate !== null && <>, poupança de {pct(current.savings_rate, 0)}</>}. Desvio padrão mensal das
        despesas: {brl(d.expense_std_monthly)} (σ = {decimal(d.expense_std_monthly, 0)}).
      </p>
      <Notes items={d.warnings} />
    </>
  );
}

export function CashflowSection() {
  const query = useCashflow();
  return (
    <Section
      id="fluxo"
      title="Fluxo de caixa"
      intro="Pagamento de fatura e transferências entre suas contas ficam de fora, para não contar o mesmo gasto duas vezes."
    >
      <Async query={query}>{(d) => <Body d={d} />}</Async>
    </Section>
  );
}
