import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCategoryChange, useConcentration, useStructure } from "../../api/hooks";
import type { ChangeMode, SpendingResponse } from "../../api/types";
import { ChartOrTable, Legend, Pills, TooltipBox } from "../../components/chart";
import type { TipProps } from "../../components/chart";
import { Async, Notes } from "../../components/ui";
import { AXIS_TICK, GRID, INTEREST_COLOR, MUTED_BAR, PRINCIPAL_COLOR, SERIES, SERIES_BLUE, SERIES_ORANGE } from "../../lib/chart";
import { MONTHS_FULL, MONTHS_SHORT, brl, brlCompactFmt, pct } from "../../lib/format";

const GAIN = "#0f7b5f";
const MODES: { value: ChangeMode; label: string }[] = [
  { value: "previous_month", label: "Mês anterior" },
  { value: "previous_year", label: "Mesmo mês do ano passado" },
  { value: "average_6m", label: "Média de 6 meses" },
];

function Block({ title, intro, children }: { title: string; intro?: string; children: React.ReactNode }) {
  return (
    <div className="mt-14">
      <h3 className="text-xl">{title}</h3>
      {intro && <p className="mt-1 max-w-[68ch] text-muted">{intro}</p>}
      <div className="mt-4">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------- cascata: receita -> categorias -> sobra
export function Waterfall({ d }: { d: SpendingResponse }) {
  if (d.income_total <= 0 && d.expense_total <= 0) return null;
  const top = d.categories.slice(0, 6);
  const others = d.expense_total - top.reduce((sum, c) => sum + c.total, 0);
  const steps: { name: string; lo: number; hi: number; value: number; color: string }[] = [];
  let level = d.income_total;
  steps.push({ name: "Recebeu", lo: 0, hi: level, value: level, color: SERIES_BLUE });
  for (const c of [...top.map((x) => ({ name: x.category, value: x.total })), ...(others > 0.5 ? [{ name: "Outras", value: others }] : [])]) {
    steps.push({ name: c.name, lo: Math.min(level, level - c.value), hi: Math.max(level, level - c.value), value: -c.value, color: PRINCIPAL_COLOR });
    level -= c.value;
  }
  steps.push({ name: level >= 0 ? "Sobrou" : "Faltou", lo: Math.min(0, level), hi: Math.max(0, level), value: level, color: level >= 0 ? GAIN : INTEREST_COLOR });
  return (
    <Block title="Do que entrou ao que sobrou" intro="Cada barra tira uma fatia da renda; o que resta no fim é o que você conseguiu guardar.">
      <ChartOrTable
        label="Cascata de receitas e gastos"
        chart={
          <div className="h-72" role="img" aria-label={`Receita de ${brl(d.income_total)}, menos gastos por categoria, resulta em ${brl(level)}.`}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={steps} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} interval={0} height={48} />
                <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
                <ReferenceLine y={0} stroke="#566370" />
                <Tooltip
                  cursor={{ fill: GRID, opacity: 0.4 }}
                  content={({ active, payload }: TipProps) => {
                    const row = payload?.[0] as { payload?: (typeof steps)[number] } | undefined;
                    if (!active || !row?.payload) return null;
                    return <TooltipBox active payload={[{ name: row.payload.name, value: Math.abs(row.payload.value), color: row.payload.color }]} format={(v) => brl(v)} />;
                  }}
                />
                <Bar dataKey={(s: (typeof steps)[number]) => [s.lo, s.hi]} isAnimationActive={false} radius={[2, 2, 2, 2]}>
                  {steps.map((s) => (
                    <Cell key={s.name} fill={s.color} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        }
        head={["Etapa", "Valor"]}
        rows={steps.map((s) => [s.name, brl(s.value)])}
      />
    </Block>
  );
}

// ---------------------------------------------------------------- variação por categoria
export function CategoryChangeBlock({ d }: { d: SpendingResponse }) {
  const [mode, setMode] = useState<ChangeMode>("previous_month");
  const now = new Date();
  const month = d.month ?? (d.year === now.getFullYear() ? now.getMonth() + 1 : 12);
  const query = useCategoryChange(d.year, month, mode);
  return (
    <Block
      title={`O que mudou em ${MONTHS_FULL[month - 1]}`}
      intro={d.month ? "Quanto cada categoria subiu ou caiu contra o período escolhido." : "Como você escolheu o ano todo, comparamos o mês mais recente do ano."}
    >
      <Pills label="Comparar com" small options={MODES} value={mode} onChange={setMode} />
      <div aria-busy={query.isFetching} className={`mt-4 ${query.isPlaceholderData ? "opacity-60" : ""}`}>
        <Async query={query}>
          {(c) => {
            const rows = c.rows.filter((r) => r.delta !== 0).slice(0, 12);
            const up = c.current_total - c.base_total;
            return rows.length === 0 ? (
              <p className="text-muted">Sem diferenças para mostrar neste período.</p>
            ) : (
              <>
                <p className="num max-w-[68ch]">
                  No total, {brl(Math.abs(up))} {up >= 0 ? "a mais" : "a menos"} que {c.base_label} ({brl(c.current_total)} contra {brl(c.base_total)}).
                </p>
                <div className="mt-3">
                  <ChartOrTable
                    label="Variação por categoria"
                    chart={
                      <div style={{ height: 56 + rows.length * 30 }} role="img" aria-label="Quanto cada categoria subiu ou caiu.">
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 64, bottom: 0, left: 0 }}>
                            <CartesianGrid stroke={GRID} horizontal={false} />
                            <XAxis type="number" tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} />
                            <YAxis type="category" dataKey="category" tick={AXIS_TICK} tickLine={false} axisLine={false} width={110} />
                            <ReferenceLine x={0} stroke="#566370" />
                            <Tooltip
                              cursor={{ fill: GRID, opacity: 0.4 }}
                              content={({ active, payload }: TipProps) => {
                                const row = payload?.[0] as { payload?: (typeof rows)[number] } | undefined;
                                if (!active || !row?.payload) return null;
                                const r = row.payload;
                                return (
                                  <div className="pointer-events-none border border-line bg-surface px-3 py-2 text-sm shadow-sm">
                                    <p className="font-medium">{r.category}</p>
                                    <p className="num">{r.delta >= 0 ? "+" : "−"}{brl(Math.abs(r.delta))}{r.delta_pct !== null && ` (${r.delta >= 0 ? "+" : "−"}${pct(Math.abs(r.delta_pct), 0)})`}</p>
                                    <p className="num text-muted">{brl(r.current)} agora · {brl(r.base)} antes</p>
                                  </div>
                                );
                              }}
                            />
                            <Bar
                              dataKey="delta"
                              name="Variação"
                              radius={2}
                              isAnimationActive={false}
                              label={{ position: "right", fill: "#1d2733", fontSize: 12, formatter: (v: unknown) => `${Number(v) >= 0 ? "+" : "−"}${brlCompactFmt(Math.abs(Number(v)))}` }}
                            >
                              {rows.map((r) => (
                                <Cell key={r.category} fill={r.delta > 0 ? INTEREST_COLOR : GAIN} />
                              ))}
                            </Bar>
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                    }
                    head={["Categoria", "Agora", "Antes", "Diferença"]}
                    rows={rows.map((r) => [r.category, brl(r.current), brl(r.base), `${r.delta >= 0 ? "+" : "−"}${brl(Math.abs(r.delta))}`])}
                  />
                </div>
                <p className="mt-2 text-sm text-muted">Vermelho: gastou mais. Verde: gastou menos.</p>
                <Notes items={c.warnings} />
              </>
            );
          }}
        </Async>
      </div>
    </Block>
  );
}

// ---------------------------------------------------------------- fixo x variável x avulso
export function StructureBlock({ year }: { year: number }) {
  const query = useStructure(year);
  return (
    <Block title="O quanto do gasto já estava travado" intro="Fixo repete todo mês; variável é o dia a dia; avulso é um lançamento grande fora do padrão.">
      <div className={query.isPlaceholderData ? "opacity-60" : undefined}>
        <Async query={query}>
          {(s) => {
            const data = s.months.map((m) => ({ ...m, name: MONTHS_SHORT[m.month - 1] }));
            if (s.fixed_total + s.variable_total + s.one_off_total === 0) return <p className="text-muted">Nenhum gasto em {s.year}.</p>;
            return (
              <>
                <p className="num max-w-[68ch]">
                  {s.fixed_share !== null && <>{pct(s.fixed_share, 0)} do que você gastou em {s.year} já estava comprometido com contas fixas ({brl(s.fixed_total)}). </>}
                  Lançamentos avulsos acima de {brl(s.one_off_threshold)} somaram {brl(s.one_off_total)}.
                </p>
                <div className="mt-3">
                  <ChartOrTable
                    label="Gasto por mês: fixo, variável e avulso"
                    chart={
                      <div>
                        <Legend items={[{ label: "Fixo", color: SERIES_BLUE }, { label: "Variável", color: SERIES_ORANGE }, { label: "Avulso", color: SERIES[2] }]} />
                        <div className="mt-3 h-64" role="img" aria-label="Gasto mensal dividido em fixo, variável e avulso.">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                              <CartesianGrid stroke={GRID} vertical={false} />
                              <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} />
                              <YAxis tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} />
                              <Tooltip cursor={{ fill: GRID, opacity: 0.4 }} content={(p: TipProps) => <TooltipBox {...p} format={(v) => brl(v)} />} />
                              <Bar dataKey="fixed" name="Fixo" stackId="s" fill={SERIES_BLUE} isAnimationActive={false} />
                              <Bar dataKey="variable" name="Variável" stackId="s" fill={SERIES_ORANGE} isAnimationActive={false} />
                              <Bar dataKey="one_off" name="Avulso" stackId="s" fill={SERIES[2]} isAnimationActive={false} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    }
                    head={["Mês", "Fixo", "Variável", "Avulso", "Total"]}
                    rows={data.map((m) => [m.name, brl(m.fixed), brl(m.variable), brl(m.one_off), brl(m.total)])}
                  />
                </div>
                {s.fixed_items.length > 0 && <p className="mt-2 max-w-[80ch] text-sm text-muted">Fixos identificados: {s.fixed_items.join(", ")}.</p>}
                <Notes items={s.warnings.slice(0, 1)} />
              </>
            );
          }}
        </Async>
      </div>
    </Block>
  );
}

// ---------------------------------------------------------------- concentração (Pareto)
export function ParetoBlock({ d }: { d: SpendingResponse }) {
  const [by, setBy] = useState<"categories" | "merchants">("categories");
  const query = useConcentration(d.year, d.month);
  return (
    <Block title="Poucos itens explicam quase tudo" intro="As barras são os maiores gastos; a linha mostra quanto do total já foi explicado até ali.">
      <Pills
        label="Agrupar por"
        small
        options={[{ value: "categories" as const, label: "Categorias" }, { value: "merchants" as const, label: "Estabelecimentos" }]}
        value={by}
        onChange={setBy}
      />
      <div className={`mt-4 ${query.isPlaceholderData ? "opacity-60" : ""}`}>
        <Async query={query}>
          {(c) => {
            const series = c[by];
            const noun = by === "categories" ? "categorias" : "estabelecimentos";
            if (series.items.length === 0) return <p className="text-muted">Nenhum gasto neste período.</p>;
            const items = series.items.slice(0, 15).map((i) => ({ ...i, pctCum: i.cumulative * 100, pctShare: i.share * 100 }));
            return (
              <>
                <p className="num max-w-[68ch]">
                  {series.n_for_50} de {series.count} {noun} respondem por metade do gasto; {series.n_for_80} chegam a 80%.
                </p>
                <div className="mt-3">
                  <ChartOrTable
                    label="Curva de Pareto"
                    chart={
                      <div>
                        <Legend items={[{ label: "Parte de cada item", color: SERIES_BLUE }, { label: "% acumulado", color: INTEREST_COLOR }]} />
                        <div className="mt-3 h-72" role="img" aria-label={`Maiores ${noun} e o percentual acumulado do gasto.`}>
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={items} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
                              <CartesianGrid stroke={GRID} vertical={false} />
                              <XAxis dataKey="name" tick={AXIS_TICK} tickLine={false} interval={0} angle={-30} textAnchor="end" height={70} />
                              <YAxis tickFormatter={(v: number) => `${v}%`} tick={AXIS_TICK} tickLine={false} axisLine={false} width={48} domain={[0, 100]} />
                              <Tooltip
                                cursor={{ fill: GRID, opacity: 0.4 }}
                                content={({ active, payload }: TipProps) => {
                                  const row = payload?.[0] as { payload?: (typeof items)[number] } | undefined;
                                  if (!active || !row?.payload) return null;
                                  const r = row.payload;
                                  return (
                                    <div className="pointer-events-none border border-line bg-surface px-3 py-2 text-sm shadow-sm">
                                      <p className="font-medium">{r.name}</p>
                                      <p className="num">{brl(r.total)} · {pct(r.share, 1)} do total</p>
                                      <p className="num text-muted">{pct(r.cumulative, 0)} acumulado</p>
                                    </div>
                                  );
                                }}
                              />
                              <Bar dataKey="pctShare" name="Parte do gasto" radius={[2, 2, 0, 0]} isAnimationActive={false}>
                                {items.map((i, k) => (
                                  <Cell key={i.name} fill={k < series.n_for_80 ? SERIES_BLUE : MUTED_BAR} />
                                ))}
                              </Bar>
                              <Line dataKey="pctCum" name="Acumulado" stroke={INTEREST_COLOR} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false}
                                label={{ position: "top", fill: INTEREST_COLOR, fontSize: 11, formatter: (v: unknown) => `${Math.round(Number(v))}%` }} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    }
                    head={["Item", "Gasto", "Parte", "Acumulado"]}
                    rows={items.map((i) => [i.name, brl(i.total), pct(i.share, 1), pct(i.cumulative, 0)])}
                  />
                </div>
                <p className="mt-2 text-sm text-muted">Barras azuis: os itens que, somados, chegam a 80% do gasto.</p>
              </>
            );
          }}
        </Async>
      </div>
    </Block>
  );
}
