import { useEffect, useMemo, useState } from "react";
import { Layer, Rectangle, ResponsiveContainer, Sankey, Tooltip } from "recharts";

import { useYearSummary } from "../api/hooks";
import type { YearSummaryResponse } from "../api/types";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { brl, pct } from "../lib/format";

const TOP_CATEGORIES_IN_FLOW = 8;
const TOP_CATEGORIES_IN_GRID = 12;
const MONTHS_SHORT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];
const integer = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });

// ---------------------------------------------------------------- seletor de ano
function YearPicker({
  years,
  value,
  onChange,
  busy,
}: {
  years: number[];
  value: number;
  onChange: (year: number) => void;
  busy: boolean;
}) {
  return (
    <div role="group" aria-label="Ano" className="mb-6 flex flex-wrap items-center gap-2">
      {years.map((year) => (
        <button
          key={year}
          type="button"
          aria-pressed={year === value}
          onClick={() => onChange(year)}
          className={`num rounded-sm border px-3 py-1 text-sm ${
            year === value ? "border-ink bg-ink text-surface" : "border-line hover:border-ink"
          }`}
        >
          {year}
        </button>
      ))}
      {busy && (
        <span role="status" className="ml-2 text-sm text-muted">
          Atualizando…
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- fluxo (Sankey)
type Kind = "income" | "deficit" | "middle" | "category" | "other" | "leftover";
interface FlowNode {
  name: string;
  kind: Kind;
}
interface FlowLink {
  source: number;
  target: number;
  value: number;
}

const NODE_FILL: Record<Kind, string> = {
  income: "var(--color-gain)",
  deficit: "var(--color-loss)",
  middle: "var(--color-ink)",
  category: "var(--color-accent)",
  other: "var(--color-muted)",
  leftover: "var(--color-gain)",
};

function rankedCategories(d: YearSummaryResponse) {
  const top = d.categories.filter((c) => c.total > 0).slice(0, TOP_CATEGORIES_IN_FLOW);
  const other = d.expense_total - top.reduce((sum, c) => sum + c.total, 0);
  return { top, other };
}

/** Receitas (e reserva, se gastou mais do que entrou) -> dinheiro do ano -> categorias e sobra. */
function buildFlow(d: YearSummaryResponse): { nodes: FlowNode[]; links: FlowLink[] } {
  const nodes: FlowNode[] = [];
  const links: FlowLink[] = [];
  const add = (name: string, kind: Kind): number => nodes.push({ name, kind }) - 1;

  const leftover = Math.max(d.income_total - d.expense_total, 0);
  const deficit = Math.max(d.expense_total - d.income_total, 0);

  const income = d.income_total > 0 ? add("Receitas", "income") : -1;
  const reserve = deficit > 0.5 ? add("Reserva ou crédito", "deficit") : -1;
  const middle = add("Dinheiro do ano", "middle");
  if (income >= 0) links.push({ source: income, target: middle, value: d.income_total });
  if (reserve >= 0) links.push({ source: reserve, target: middle, value: deficit });

  const { top, other } = rankedCategories(d);
  for (const c of top) links.push({ source: middle, target: add(c.category, "category"), value: c.total });
  if (other > 0.5) links.push({ source: middle, target: add("Outras categorias", "other"), value: other });
  if (leftover > 0.5) links.push({ source: middle, target: add("Sobrou", "leftover"), value: leftover });
  return { nodes, links };
}

interface FlowNodeProps {
  x: number;
  y: number;
  width: number;
  height: number;
  payload: FlowNode & { depth: number; value: number };
}

function FlowNodeShape({ x, y, width, height, payload }: FlowNodeProps) {
  const onLeft = payload.depth === 0;
  const labelX = onLeft ? x - 8 : x + width + 8;
  return (
    <Layer>
      <Rectangle x={x} y={y} width={width} height={height} fill={NODE_FILL[payload.kind]} fillOpacity={0.92} />
      {payload.kind !== "middle" && (
        <text
          x={labelX}
          y={y + height / 2}
          textAnchor={onLeft ? "end" : "start"}
          dominantBaseline="middle"
          fontSize={12}
          fill="var(--color-ink)"
        >
          <tspan x={labelX} dy="-0.5em">
            {payload.name}
          </tspan>
          <tspan x={labelX} dy="1.25em" fill="var(--color-muted)">
            {brl(payload.value)}
          </tspan>
        </text>
      )}
    </Layer>
  );
}

/** true a partir de 640 px (breakpoint `sm`). Evita montar o Sankey dentro de um contêiner escondido. */
function useIsWide(): boolean {
  const query = "(min-width: 640px)";
  const [wide, setWide] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const onChange = () => setWide(media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);
  return wide;
}

function FlowChart({ d }: { d: YearSummaryResponse }) {
  const { nodes, links } = useMemo(() => buildFlow(d), [d]);
  const destinations = nodes.filter((n) => n.kind === "category" || n.kind === "other" || n.kind === "leftover").length;
  if (links.length === 0) return null;
  return (
    <div
      style={{ height: Math.max(300, 70 + destinations * 46) }}
      role="img"
      aria-label={`Fluxo do dinheiro em ${d.year}: de onde veio e para onde foi`}
    >
      <ResponsiveContainer width="100%" height="100%">
        <Sankey
          data={{ nodes, links }}
          nodePadding={26}
          nodeWidth={12}
          margin={{ top: 12, right: 170, bottom: 32, left: 120 }}
          link={{ stroke: "var(--color-band)", strokeOpacity: 0.95 }}
          node={FlowNodeShape as never}
        >
          <Tooltip formatter={(value) => brl(Number(value))} />
        </Sankey>
      </ResponsiveContainer>
    </div>
  );
}

/** Versão para telas estreitas: o Sankey não cabe, então vira uma lista ordenada. */
function RankedList({ d }: { d: YearSummaryResponse }) {
  const { top, other } = rankedCategories(d);
  const rows = [...top.map((c) => ({ name: c.category, total: c.total })), ...(other > 0.5 ? [{ name: "Outras categorias", total: other }] : [])];
  const max = Math.max(...rows.map((r) => r.total), 1);
  return (
    <ol className="space-y-3" aria-label="Despesas por categoria">
      {rows.map((r) => (
        <li key={r.name}>
          <div className="flex items-baseline justify-between gap-3 text-sm">
            <span>{r.name}</span>
            <span className="num">
              {brl(r.total)} <span className="text-muted">({pct(r.total / d.expense_total, 0)})</span>
            </span>
          </div>
          <div className="mt-1 h-1.5 bg-line/70">
            <div className="h-full bg-accent" style={{ width: `${(r.total / max) * 100}%` }} />
          </div>
        </li>
      ))}
    </ol>
  );
}

// ---------------------------------------------------------------- grade categoria x mês
function heat(ratio: number): { background?: string; color?: string } {
  if (ratio <= 0) return {};
  const strength = Math.round(8 + 72 * Math.min(ratio, 1));
  return {
    background: `color-mix(in srgb, var(--color-accent) ${strength}%, transparent)`,
    color: strength > 48 ? "var(--color-surface)" : undefined,
  };
}

function Heatmap({ d }: { d: YearSummaryResponse }) {
  const top = d.categories.filter((c) => c.total > 0).slice(0, TOP_CATEGORIES_IN_GRID);
  const restMonthly = d.months.map((m, i) => m.expense - top.reduce((sum, c) => sum + (c.monthly[i] ?? 0), 0));
  const restTotal = d.expense_total - top.reduce((sum, c) => sum + c.total, 0);
  const showRest = Math.abs(restTotal) > 0.5 || restMonthly.some((v) => Math.abs(v) > 0.5);

  const rows = [
    ...top.map((c) => ({ name: c.category, monthly: c.monthly, total: c.total, share: c.share })),
    ...(showRest
      ? [{ name: "Outras categorias", monthly: restMonthly, total: restTotal, share: d.expense_total > 0 ? restTotal / d.expense_total : null }]
      : []),
  ];

  const hasData = d.months.map((m) => m.has_data);
  const cell = (value: number, i: number, rowMax: number) => {
    if (!hasData[i]) return <td key={i} className="px-2 py-1.5 text-right text-muted/60" title="Sem transações importadas">–</td>;
    if (Math.abs(value) < 0.5) return <td key={i} className="px-2 py-1.5 text-right text-muted/60">–</td>;
    return (
      <td key={i} className="px-2 py-1.5 text-right" style={heat(value / rowMax)}>
        {integer.format(value)}
      </td>
    );
  };

  const stickyHead = "sticky left-0 z-[1] bg-paper py-1.5 pr-3 text-left font-normal";
  return (
    <div className="mt-10">
      <h3 className="text-xl">Categoria por mês</h3>
      <p className="mt-1 text-sm text-muted">
        Valores em reais. Quanto mais escuro, mais aquela categoria pesou naquele mês em relação ao próprio ano.
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="num w-full min-w-[920px] border-collapse text-sm">
          <caption className="sr-only">Despesas de {d.year} por categoria e mês, em reais</caption>
          <thead>
            <tr className="border-b border-line text-muted">
              <th scope="col" className="sticky left-0 z-[1] bg-paper py-2 pr-3 text-left font-medium">Categoria</th>
              {MONTHS_SHORT.map((m) => (
                <th key={m} scope="col" className="px-2 py-2 text-right font-medium">{m}</th>
              ))}
              <th scope="col" className="px-2 py-2 text-right font-medium">Total</th>
              <th scope="col" className="py-2 pl-2 text-right font-medium">% das despesas</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const rowMax = Math.max(...r.monthly, 1);
              return (
                <tr key={r.name} className="border-b border-line/50">
                  <th scope="row" className={stickyHead}>{r.name}</th>
                  {r.monthly.map((v, i) => cell(v, i, rowMax))}
                  <td className="px-2 py-1.5 text-right font-medium">{integer.format(r.total)}</td>
                  <td className="py-1.5 pl-2 text-right text-muted">{r.share === null ? "—" : pct(r.share, 1)}</td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="border-t border-ink">
              <th scope="row" className={`${stickyHead} font-medium`}>Despesas</th>
              {d.months.map((m, i) => (
                <td key={m.month} className="px-2 py-1.5 text-right">{hasData[i] ? integer.format(m.expense) : "–"}</td>
              ))}
              <td className="px-2 py-1.5 text-right font-medium">{integer.format(d.expense_total)}</td>
              <td />
            </tr>
            <tr>
              <th scope="row" className={`${stickyHead} font-medium`}>Receitas</th>
              {d.months.map((m, i) => (
                <td key={m.month} className="px-2 py-1.5 text-right">{hasData[i] ? integer.format(m.income) : "–"}</td>
              ))}
              <td className="px-2 py-1.5 text-right font-medium">{integer.format(d.income_total)}</td>
              <td />
            </tr>
            <tr>
              <th scope="row" className={`${stickyHead} font-medium`}>Sobrou</th>
              {d.months.map((m, i) => (
                <td key={m.month} className={`px-2 py-1.5 text-right ${m.savings >= 0 ? "text-gain" : "text-loss"}`}>
                  {hasData[i] ? integer.format(m.savings) : "–"}
                </td>
              ))}
              <td className={`px-2 py-1.5 text-right font-medium ${d.savings_total >= 0 ? "text-gain" : "text-loss"}`}>
                {integer.format(d.savings_total)}
              </td>
              <td />
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- corpo e seção
function Body({ d, otherYears }: { d: YearSummaryResponse; otherYears: number[] }) {
  const wide = useIsWide(); // hooks sempre antes de qualquer return antecipado
  if (d.months_with_data.length === 0) {
    return (
      <p className="max-w-[62ch] text-muted">
        Não há transações importadas em {d.year}.
        {otherYears.length > 0 && <> Há dados em {otherYears.join(", ")}.</>} Para trazer mais histórico, sincronize com a
        opção de histórico completo.
      </p>
    );
  }
  const biggest = d.categories.find((c) => c.total > 0);
  return (
    <>
      <FigureRow>
        <Figure
          label={`Entrou em ${d.year}`}
          value={brl(d.income_total)}
          hint={`${d.months_with_data.length} ${d.months_with_data.length === 1 ? "mês" : "meses"} com dados`}
        />
        <Figure label="Saiu" value={brl(d.expense_total)} hint="sem fatura paga nem transferência própria" />
        <Figure
          label={d.savings_total >= 0 ? "Sobrou" : "Faltou"}
          value={brl(Math.abs(d.savings_total))}
          hint={
            d.savings_rate === null
              ? undefined
              : d.savings_total >= 0
                ? `${pct(d.savings_rate, 0)} da renda`
                : "gastou mais do que entrou"
          }
        />
        <Figure
          label="Maior gasto"
          value={<span className="text-[1.4rem]">{biggest?.category ?? "—"}</span>}
          hint={biggest ? `${brl(biggest.total)}, ${pct(biggest.share, 0)} das despesas` : undefined}
        />
      </FigureRow>

      <div className="mt-8">
        {wide ? <FlowChart d={d} /> : <RankedList d={d} />}
      </div>
      <Heatmap d={d} />
      <Notes items={d.warnings} />
    </>
  );
}

export function YearSection() {
  const thisYear = new Date().getFullYear();
  const [year, setYear] = useState(thisYear);
  const query = useYearSummary(year);

  const available = query.data?.available_years ?? [];
  const years = useMemo(
    () => [...new Set([thisYear, year, ...available])].sort((a, b) => b - a),
    [thisYear, year, available],
  );

  return (
    <Section
      id="ano"
      title="Para onde foi o dinheiro"
      intro="Tudo o que entrou e saiu no ano, por categoria e por mês. Troque o ano para comparar."
    >
      <YearPicker years={years} value={year} onChange={setYear} busy={query.isFetching && query.isPlaceholderData} />
      <div aria-busy={query.isFetching} className={query.isPlaceholderData ? "opacity-60" : undefined}>
        <Async query={query}>{(d) => <Body d={d} otherYears={available.filter((y) => y !== d.year)} />}</Async>
      </div>
    </Section>
  );
}
