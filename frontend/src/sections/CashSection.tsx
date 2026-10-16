import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useCashForecast, useInstallments, usePace } from "../api/hooks";
import type { CashForecastResponse, InstallmentsResponse, PaceResponse } from "../api/types";
import { ChartOrTable, Legend, TooltipBox } from "../components/chart";
import type { TipProps } from "../components/chart";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { AXIS_TICK, GRID, SERIES_BLUE, SERIES_ORANGE, STRATEGY_COLOR } from "../lib/chart";
import { brl, brlCompactFmt, dayMonth, fullDate, monthLabel } from "../lib/format";

const BAND = "#cfe0f6"; // faixa de incerteza: o azul da linha, bem claro
const KIND_LABEL = { recurring: "Conta fixa", income: "Entrada", loan: "Parcela", card: "Fatura" } as const;

// ---------------------------------------------------------------- projeção de saldo
function ForecastHeadline({ d }: { d: CashForecastResponse }) {
  const negative = d.first_negative_base;
  const risk = d.first_negative_low;
  return (
    <p className="num max-w-[68ch] font-serif text-2xl leading-snug">
      {negative
        ? `No ritmo atual, o saldo fica negativo em ${fullDate(negative)}.`
        : `Em ${d.days} dias o saldo da conta deve ir de ${brl(d.starting_balance)} para ${brl(d.ending_balance)}.`}{" "}
      {!negative && risk && `Num cenário de gasto alto, ele fica negativo em ${fullDate(risk)}. `}
      O ponto mais baixo é {brl(d.min_balance)} em {dayMonth(d.min_date)}.
    </p>
  );
}

function ForecastChart({ d }: { d: CashForecastResponse }) {
  const data = d.points.map((p) => ({ ...p, band: p.high - p.low }));
  return (
    <div>
      <Legend
        shape="line"
        items={[
          { label: "Saldo previsto", color: SERIES_BLUE },
          { label: "Faixa provável (gasto menor a maior)", color: BAND },
        ]}
      />
      <div className="mt-3 h-72" role="img" aria-label={`Saldo diário previsto para ${d.days} dias, de ${brl(d.starting_balance)} a ${brl(d.ending_balance)}.`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="date" tickFormatter={dayMonth} tick={AXIS_TICK} tickLine={false} minTickGap={36} />
            <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
            <ReferenceLine y={0} stroke="#566370" />
            <Tooltip
              content={(p: TipProps) => (
                <TooltipBox
                  {...p}
                  title={(l) => fullDate(String(l))}
                  format={(v) => brl(v)}
                />
              )}
            />
            {/* a faixa é empilhada: base transparente em `low` + largura `band` */}
            <Area dataKey="low" stackId="b" stroke="none" fill="transparent" name="Cenário de gasto alto" isAnimationActive={false} />
            <Area dataKey="band" stackId="b" stroke="none" fill={BAND} fillOpacity={0.8} legendType="none" tooltipType="none" isAnimationActive={false} />
            <Line dataKey="base" name="Saldo previsto" stroke={SERIES_BLUE} strokeWidth={2} dot={false} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function EventsTable({ d }: { d: CashForecastResponse }) {
  if (d.events.length === 0) return null;
  return (
    <div className="mt-8">
      <h3 className="text-lg">O que está previsto para cair</h3>
      <div className="mt-2 max-h-72 overflow-auto">
        <table className="num w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th className="py-1.5 pr-4 font-medium">Dia</th>
              <th className="py-1.5 pr-4 font-medium">O quê</th>
              <th className="py-1.5 pr-4 font-medium">Tipo</th>
              <th className="py-1.5 text-right font-medium">Valor</th>
            </tr>
          </thead>
          <tbody>
            {d.events.map((e) => (
              <tr key={`${e.date}-${e.label}`} className="border-b border-line/60">
                <td className="py-1.5 pr-4">{dayMonth(e.date)}</td>
                <td className="py-1.5 pr-4">{e.label}</td>
                <td className="py-1.5 pr-4 text-muted">{KIND_LABEL[e.kind]}</td>
                <td className={`py-1.5 text-right ${e.amount > 0 ? "text-gain" : ""}`}>{brl(e.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Forecast({ d }: { d: CashForecastResponse }) {
  return (
    <div>
      <ForecastHeadline d={d} />
      <div className="mt-6">
        <FigureRow>
          <Figure label="Saldo hoje" value={brl(d.starting_balance)} hint="contas correntes" />
          <Figure label={`Em ${d.days} dias`} value={brl(d.ending_balance)} />
          <Figure label="Ponto mais baixo" value={brl(d.min_balance)} hint={dayMonth(d.min_date)} />
          <Figure label="Gasto do dia a dia" value={`${brl(d.daily_variable_spend)}/dia`} hint="média dos últimos 90 dias, sem fixos" />
        </FigureRow>
      </div>
      <div className="mt-8">
        <ChartOrTable
          label="Saldo diário previsto"
          chart={<ForecastChart d={d} />}
          head={["Dia", "Previsto", "Gasto alto", "Gasto baixo"]}
          rows={d.points.filter((_, i) => i % 3 === 2).map((p) => [dayMonth(p.date), brl(p.base), brl(p.low), brl(p.high)])}
        />
      </div>
      <EventsTable d={d} />
      <Notes items={d.warnings} />
    </div>
  );
}

// ---------------------------------------------------------------- ritmo do mês
function PaceBlock({ d }: { d: PaceResponse }) {
  const data = d.days.map((day, i) => ({ day, current: d.current[i], previous: d.previous[i], average: d.average[i] }));
  const ahead = d.average_to_date !== null && d.spent_to_date > d.average_to_date;
  const diff = d.average_to_date === null ? null : Math.abs(d.spent_to_date - d.average_to_date);
  const hasHistory = d.months_in_average > 0;
  return (
    <div className="mt-14">
      <h3 className="text-xl">Este mês contra o seu normal</h3>
      {hasHistory && diff !== null ? (
        <p className="num mt-1 max-w-[68ch] text-muted">
          Até o dia {d.day_of_month} você gastou {brl(d.spent_to_date)}: {brl(diff)} {ahead ? "acima" : "abaixo"} do que costuma gastar até aqui
          {d.projected_end !== null && <>. Nesse ritmo, o mês fecha perto de {brl(d.projected_end)} (o normal é {brl(d.typical_end)})</>}.
        </p>
      ) : (
        <p className="mt-1 text-muted">Ainda não há meses fechados para comparar.</p>
      )}
      {hasHistory && (
        <div className="mt-4">
          <ChartOrTable
            label="Gasto acumulado no mês"
            chart={
              <div>
                <Legend
                  shape="line"
                  items={[
                    { label: "Este mês", color: SERIES_BLUE },
                    ...(d.previous_label ? [{ label: `Mês passado (${d.previous_label})`, color: SERIES_ORANGE }] : []),
                    { label: `Média de ${d.months_in_average} meses`, color: STRATEGY_COLOR.baseline },
                  ]}
                />
                <div className="mt-3 h-64" role="img" aria-label="Gasto acumulado por dia do mês: este mês, mês passado e média.">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="day" tick={AXIS_TICK} tickLine={false} />
                      <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
                      <Tooltip content={(p: TipProps) => <TooltipBox {...p} title={(l) => `Dia ${l}`} format={(v) => brl(v)} />} />
                      <Line dataKey="average" name="Média" stroke={STRATEGY_COLOR.baseline} strokeWidth={1.5} strokeDasharray="5 4" dot={false} isAnimationActive={false} />
                      <Line dataKey="previous" name="Mês passado" stroke={SERIES_ORANGE} strokeWidth={1.5} dot={false} isAnimationActive={false} />
                      <Line dataKey="current" name="Este mês" stroke={SERIES_BLUE} strokeWidth={2.5} dot={false} connectNulls={false} isAnimationActive={false} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            }
            head={["Dia", "Este mês", "Mês passado", "Média"]}
            rows={data.filter((r) => r.day % 5 === 0 || r.day === d.day_of_month).map((r) => [r.day, r.current === null ? "—" : brl(r.current), brl(r.previous), brl(r.average)])}
          />
        </div>
      )}
      <Notes items={d.warnings} />
    </div>
  );
}

// ---------------------------------------------------------------- parcelados
function InstallmentsBlock({ d }: { d: InstallmentsResponse }) {
  return (
    <div className="mt-14">
      <h3 className="text-xl">Parcelado no cartão: o que já está comprometido</h3>
      {d.purchases.length === 0 ? (
        <p className="mt-1 max-w-[62ch] text-muted">{d.warnings[0] ?? "Nenhuma compra parcelada com parcelas futuras."}</p>
      ) : (
        <>
          <p className="num mt-1 max-w-[68ch] text-muted">
            Já estão contratados {brl(d.total_committed)} em parcelas futuras; no mês que vem caem {brl(d.next_month)}, antes de qualquer compra nova.
          </p>
          <div className="mt-4 grid gap-y-8 lg:grid-cols-2 lg:gap-x-10">
            <ChartOrTable
              label="Parcelas por mês"
              chart={
                <div className="h-60" role="img" aria-label="Valor de parcelas que cairão em cada mês.">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={d.months} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                      <CartesianGrid stroke={GRID} vertical={false} />
                      <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS_TICK} tickLine={false} />
                      <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
                      <Tooltip cursor={{ fill: GRID, opacity: 0.4 }} content={(p: TipProps) => <TooltipBox {...p} title={(l) => monthLabel(String(l))} format={(v) => brl(v)} />} />
                      <Bar dataKey="total" name="Parcelas" fill={SERIES_BLUE} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              }
              head={["Mês", "Parcelas", "Compras"]}
              rows={d.months.map((m) => [monthLabel(m.month), brl(m.total), m.count])}
            />
            <div className="max-h-72 overflow-auto">
              <table className="num w-full text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-muted">
                    <th className="py-1.5 pr-3 font-medium">Compra</th>
                    <th className="py-1.5 pr-3 text-right font-medium">Parcela</th>
                    <th className="py-1.5 pr-3 text-right font-medium">Faltam</th>
                    <th className="py-1.5 text-right font-medium">Total restante</th>
                  </tr>
                </thead>
                <tbody>
                  {d.purchases.map((p) => (
                    <tr key={`${p.description}-${p.account}-${p.total}-${p.amount}`} className="border-b border-line/60">
                      <td className="py-1.5 pr-3">
                        {p.description}
                        <span className="block text-xs text-muted">até {monthLabel(p.ends_on)}</span>
                      </td>
                      <td className="py-1.5 pr-3 text-right">{brl(p.amount)}</td>
                      <td className="py-1.5 pr-3 text-right">{p.remaining}x</td>
                      <td className="py-1.5 text-right">{brl(p.remaining_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export function CashSection() {
  const forecast = useCashForecast();
  const pace = usePace();
  const installments = useInstallments();
  return (
    <Section
      id="caixa"
      title="Caixa: os próximos dias"
      intro="O que deve acontecer com o saldo, se este mês está acima ou abaixo do normal e quanto do futuro já está parcelado."
    >
      <Async query={forecast}>{(d) => <Forecast d={d} />}</Async>
      <Async query={pace}>{(d) => <PaceBlock d={d} />}</Async>
      <Async query={installments}>{(d) => <InstallmentsBlock d={d} />}</Async>
    </Section>
  );
}
