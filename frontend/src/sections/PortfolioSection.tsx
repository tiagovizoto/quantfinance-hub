import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useNetWorth, usePortfolio } from "../api/hooks";
import type { Assumptions, NetWorthResponse, PortfolioResponse } from "../api/types";
import { ChartOrTable, TooltipBox } from "../components/chart";
import type { TipProps } from "../components/chart";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { AXIS_TICK, GRID, MUTED_BAR, SERIES, SERIES_BLUE } from "../lib/chart";
import { brl, brlCompactFmt, dayMonth, decimal, monthLabel, pct } from "../lib/format";

const SOURCE_TEXT = {
  realizada_12m: "rendeu nos últimos 12 meses",
  contratada: "taxa contratada",
  estimada: "estimada",
} as const;

// ---------------------------------------------------------------- composição
function Composition({ d }: { d: PortfolioResponse }) {
  return (
    <div>
      <h3 className="text-lg">Onde está o dinheiro</h3>
      <ChartOrTable
        label="Carteira por classe de ativo"
        chart={
          <div className="mt-2" style={{ height: 52 + d.by_class.length * 36 }} role="img" aria-label="Valor investido por classe de ativo.">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={d.by_class} layout="vertical" margin={{ top: 4, right: 72, bottom: 0, left: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} width={110} />
                <Tooltip cursor={{ fill: GRID, opacity: 0.4 }} content={(p: TipProps) => <TooltipBox {...p} format={(v) => brl(v)} />} />
                <Bar
                  dataKey="value"
                  name="Valor"
                  radius={[0, 2, 2, 0]}
                  isAnimationActive={false}
                  label={{ position: "right", fill: "#1d2733", fontSize: 12, formatter: (v: unknown) => brlCompactFmt(Number(v)) }}
                >
                  {d.by_class.map((c, i) => (
                    <Cell key={c.asset_class} fill={SERIES[i % SERIES.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        }
        head={["Classe", "Valor", "Parte"]}
        rows={d.by_class.map((c) => [c.label, brl(c.value), pct(c.share, 0)])}
      />
    </div>
  );
}

// ---------------------------------------------------------------- vencimentos
function Maturities({ d }: { d: PortfolioResponse }) {
  return (
    <div>
      <h3 className="text-lg">Quando a renda fixa vence</h3>
      {d.maturities.length === 0 ? (
        <p className="mt-2 text-muted">Nenhuma renda fixa com vencimento informado.</p>
      ) : (
        <>
          <p className="num mt-1 text-muted">{brl(d.matures_12m)} vencem nos próximos 12 meses.</p>
          <ChartOrTable
            label="Vencimentos por mês"
            chart={
              <div className="mt-2 h-52" role="img" aria-label="Valor de renda fixa que vence em cada mês.">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={d.maturities} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS_TICK} tickLine={false} />
                    <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
                    <Tooltip
                      cursor={{ fill: GRID, opacity: 0.4 }}
                      content={(p: TipProps) => {
                        const bucket = d.maturities.find((m) => m.month === p.label);
                        return (
                          <div>
                            <TooltipBox {...p} title={(l) => monthLabel(String(l))} format={(v) => brl(v)} />
                            {bucket && <p className="pointer-events-none border border-t-0 border-line bg-surface px-3 py-1 text-xs text-muted">{bucket.items.join(", ")}</p>}
                          </div>
                        );
                      }}
                    />
                    <Bar dataKey="value" name="Vence" fill={SERIES_BLUE} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            }
            head={["Mês", "Valor", "Títulos"]}
            rows={d.maturities.map((m) => [monthLabel(m.month), brl(m.value), m.items.join(", ")])}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- retorno x CDI
function VersusCdi({ d }: { d: PortfolioResponse }) {
  const rows = d.positions.filter((p) => p.rate_pct !== null);
  if (rows.length === 0) return null;
  const height = 60 + rows.length * 34;
  return (
    <div className="mt-12">
      <h3 className="text-xl">Cada investimento rende mais ou menos que o CDI?</h3>
      <p className="num mt-1 max-w-[68ch] text-muted">
        CDI de referência: {decimal(d.cdi_annual_pct, 2)}% ao ano. Em azul, quem rende acima; em cinza, quem rende abaixo. A origem da taxa está na tabela.
      </p>
      <div className="mt-4 grid gap-y-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-x-10">
        <ChartOrTable
          label="Retorno anual por investimento contra o CDI"
          chart={
            <div style={{ height }} role="img" aria-label="Retorno anual de cada investimento comparado ao CDI.">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 56, bottom: 0, left: 0 }}>
                  <CartesianGrid stroke={GRID} horizontal={false} />
                  <XAxis type="number" tickFormatter={(v: number) => `${decimal(v, 0)}%`} tick={AXIS_TICK} tickLine={false} />
                  <YAxis type="category" dataKey="name" tick={AXIS_TICK} tickLine={false} axisLine={false} width={120} />
                  <ReferenceLine x={d.cdi_annual_pct} stroke="#1d2733" strokeDasharray="4 3" label={{ value: "CDI", position: "top", fill: "#1d2733", fontSize: 12 }} />
                  <Tooltip
                    cursor={{ fill: GRID, opacity: 0.4 }}
                    content={(p: TipProps) => <TooltipBox {...p} format={(v) => `${decimal(v, 2)}% ao ano`} />}
                  />
                  <Bar
                    dataKey="rate_pct"
                    name="Retorno"
                    radius={[0, 2, 2, 0]}
                    isAnimationActive={false}
                    label={{ position: "right", fill: "#1d2733", fontSize: 12, formatter: (v: unknown) => `${decimal(Number(v), 1)}%` }}
                  >
                    {rows.map((r) => (
                      <Cell key={r.id} fill={(r.spread_vs_cdi ?? 0) >= 0 ? SERIES_BLUE : MUTED_BAR} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          }
          head={["Investimento", "Retorno", "vs CDI"]}
          rows={rows.map((r) => [r.name, `${decimal(r.rate_pct, 1)}%`, `${(r.spread_vs_cdi ?? 0) >= 0 ? "+" : ""}${decimal(r.spread_vs_cdi, 1)} p.p.`])}
        />
        <div className="max-h-80 overflow-auto">
          <table className="num w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-muted">
                <th className="py-1.5 pr-3 font-medium">Investimento</th>
                <th className="py-1.5 pr-3 text-right font-medium">Valor</th>
                <th className="py-1.5 text-right font-medium">Origem da taxa</th>
              </tr>
            </thead>
            <tbody>
              {d.positions.map((p) => (
                <tr key={p.id} className="border-b border-line/60">
                  <td className="py-1.5 pr-3">
                    {p.name}
                    {p.days_to_due !== null && <span className="block text-xs text-muted">{p.days_to_due >= 0 ? `vence em ${p.days_to_due} dias` : "vencido"}</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-right">{brl(p.value)}</td>
                  <td className="py-1.5 text-right text-muted">{p.rate_pct === null ? "—" : SOURCE_TEXT[p.rate_source]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- patrimônio ao longo do tempo
function NetWorthBlock({ d }: { d: NetWorthResponse }) {
  const c = d.current;
  const enough = d.history.length >= 2;
  const first = d.history[0];
  return (
    <div className="mt-14">
      <h3 className="text-xl">Patrimônio ao longo do tempo</h3>
      <div className="mt-4">
        <FigureRow>
          <Figure label="Patrimônio líquido" value={brl(c.net_worth)} hint={enough && first ? `${c.net_worth - first.net_worth >= 0 ? "+" : "−"}${brl(Math.abs(c.net_worth - first.net_worth))} desde ${dayMonth(first.day)}` : undefined} />
          <Figure label="Liquidez" value={brl(c.liquidity)} />
          <Figure label="Investimentos" value={brl(c.investments)} />
          <Figure label="Dívidas" value={brl(c.loans + c.card_debt)} hint="financiamentos e cartões" />
        </FigureRow>
      </div>
      {enough ? (
        <div className="mt-6">
          <ChartOrTable
            label="Patrimônio líquido por dia"
            chart={
              <div className="h-64" role="img" aria-label="Evolução do patrimônio líquido.">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={d.history} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                    <CartesianGrid stroke={GRID} vertical={false} />
                    <XAxis dataKey="day" tickFormatter={dayMonth} tick={AXIS_TICK} tickLine={false} minTickGap={32} />
                    <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} domain={["auto", "auto"]} />
                    <ReferenceLine y={0} stroke="#566370" />
                    <Tooltip content={(p: TipProps) => <TooltipBox {...p} title={(l) => dayMonth(String(l))} format={(v) => brl(v)} />} />
                    <Line dataKey="net_worth" name="Patrimônio líquido" stroke={SERIES_BLUE} strokeWidth={2} dot={d.history.length < 30} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            }
            head={["Dia", "Patrimônio", "Liquidez", "Investimentos", "Dívidas"]}
            rows={[...d.history].reverse().map((p) => [dayMonth(p.day), brl(p.net_worth), brl(p.liquidity), brl(p.investments), brl(p.loans + p.card_debt)])}
          />
        </div>
      ) : (
        <p className="mt-4 max-w-[62ch] text-muted">
          O histórico passa a ser gravado a cada sincronização, então o gráfico aparece a partir do segundo dia. Sincronize de vez em quando e a linha vai se formar.
        </p>
      )}
    </div>
  );
}

export function PortfolioSection({ assumptions }: { assumptions: Assumptions }) {
  const portfolio = usePortfolio(assumptions);
  const netWorth = useNetWorth();
  return (
    <Section
      id="carteira"
      title="Carteira e patrimônio"
      intro="Onde estão os investimentos, quando vencem, se estão rendendo mais que o CDI e como o patrimônio evolui."
    >
      <Async query={portfolio}>
        {(d) =>
          d.positions.length === 0 ? (
            <p className="text-muted">Nenhum investimento ativo encontrado.</p>
          ) : (
            <>
              <div className="grid gap-y-10 lg:grid-cols-2 lg:gap-x-10">
                <Composition d={d} />
                <Maturities d={d} />
              </div>
              <VersusCdi d={d} />
              <Notes items={d.warnings} />
            </>
          )
        }
      </Async>
      <Async query={netWorth}>{(d) => <NetWorthBlock d={d} />}</Async>
    </Section>
  );
}
