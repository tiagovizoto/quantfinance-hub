import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useMonteCarlo } from "../api/hooks";
import type { Assumptions, FanPoint, MonteCarloResponse } from "../api/types";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { brl, brlCompactFmt, pct } from "../lib/format";

interface FanDatum extends FanPoint {
  base: number;
  band: number;
}

function FanTooltip({ active, payload }: { active?: boolean; payload?: { payload: FanDatum }[] }) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;
  return (
    <div className="num border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <p className="font-medium">Daqui a {point.months} meses</p>
      <p>Otimista (P90): {brl(point.p90)}</p>
      <p>Mediana (P50): {brl(point.p50)}</p>
      <p>Pessimista (P10): {brl(point.p10)}</p>
    </div>
  );
}

function Body({ d }: { d: MonteCarloResponse }) {
  const data: FanDatum[] = d.fan.map((p) => ({ ...p, base: p.p10, band: p.p90 - p.p10 }));
  const marks = d.horizons.map((h) => h.months);
  const i = d.inputs;

  return (
    <>
      <FigureRow>
        <Figure label="Patrimônio líquido hoje" value={brl(i.initial_net_worth)} hint="ativos menos dívidas" />
        {d.horizons.map((h) => (
          <Figure
            key={h.months}
            label={`Mediana em ${h.months} meses`}
            value={brl(h.p50)}
            hint={`${brl(h.p10)} a ${brl(h.p90)}`}
          />
        ))}
      </FigureRow>

      <div className="mt-8 h-80" role="img" aria-label="Projeção do patrimônio líquido com faixa entre pessimista e otimista">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="months"
              type="number"
              domain={[1, "dataMax"]}
              ticks={marks}
              tickFormatter={(m) => `${m} m`}
              tick={{ fill: "var(--color-muted)", fontSize: 12 }}
              tickLine={false}
            />
            <YAxis
              width={72}
              tickFormatter={brlCompactFmt}
              tick={{ fill: "var(--color-muted)", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<FanTooltip />} />
            <ReferenceLine y={0} stroke="var(--color-ink)" />
            <Area dataKey="base" stackId="fan" stroke="none" fill="transparent" isAnimationActive={false} />
            <Area dataKey="band" stackId="fan" stroke="none" fill="var(--color-band)" isAnimationActive={false} />
            <Line dataKey="p50" stroke="var(--color-accent)" strokeWidth={2.5} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-sm text-muted">
        A linha é a mediana; a faixa vai do cenário pessimista (P10) ao otimista (P90), em {d.n_sims.toLocaleString("pt-BR")}{" "}
        simulações.
      </p>

      <div className="mt-6 overflow-x-auto">
        <table className="num w-full min-w-[520px] text-sm">
          <caption className="sr-only">Patrimônio projetado por horizonte</caption>
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">Horizonte</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Pessimista (P10)</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Mediana (P50)</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Otimista (P90)</th>
              <th scope="col" className="py-2 text-right font-medium">Chance de ficar negativo</th>
            </tr>
          </thead>
          <tbody>
            {d.horizons.map((h) => (
              <tr key={h.months} className="border-b border-line/60">
                <th scope="row" className="py-2 pr-4 text-left font-normal">{h.months} meses</th>
                <td className="py-2 pr-4 text-right">{brl(h.p10)}</td>
                <td className="py-2 pr-4 text-right">{brl(h.p50)}</td>
                <td className="py-2 pr-4 text-right">{brl(h.p90)}</td>
                <td className="py-2 text-right">{pct(h.prob_negative, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="num mt-5 max-w-[80ch] text-sm text-muted">
        Premissas calculadas dos seus dados: poupança mensal de {brl(i.savings_mu_monthly)} (desvio de{" "}
        {brl(i.savings_sigma_monthly)}, {i.savings_months_used} meses); carteira de {brl(i.portfolio_value)} com retorno
        líquido de {pct(i.portfolio_return_annual_net)} a.a. e volatilidade de {pct(i.portfolio_vol_annual)}. Caixa de{" "}
        {brl(i.cash)}, financiamentos de {brl(i.loans_outstanding)} e cartões de {brl(i.card_debt)} entram no patrimônio.
      </p>
      <Notes items={d.warnings} />
    </>
  );
}

export function MonteCarloSection({ assumptions }: { assumptions: Assumptions }) {
  const query = useMonteCarlo(assumptions);
  return (
    <Section
      id="projecao"
      title="Projeção do patrimônio"
      intro="Se o seu padrão de poupança e o retorno da carteira se repetirem, com a variação que já aconteceu, onde você chega."
    >
      <Async query={query}>{(d) => <Body d={d} />}</Async>
    </Section>
  );
}
