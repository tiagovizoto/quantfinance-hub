import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useSpending, useYearSummary } from "../api/hooks";
import type { RecurringExpense, SpendCategory, SpendingResponse } from "../api/types";
import { ChartOrTable, Pills, TooltipBox } from "../components/chart";
import type { TipProps } from "../components/chart";
import { Async, Figure, FigureRow, Notes, Section } from "../components/ui";
import { AXIS_TICK, GRID, MUTED_BAR, SERIES_BLUE } from "../lib/chart";
import { MONTHS_FULL, MONTHS_SHORT, brl, brlCompactFmt, dayMonth, fullDate, pct } from "../lib/format";
import { SpendCalendar } from "./explorer/Calendar";
import { CategoryChangeBlock, ParetoBlock, StructureBlock, Waterfall } from "./explorer/Insights";
import { MoneyGraph } from "./explorer/MoneyGraph";

const VISIBLE_CATEGORIES = 10;

// ---------------------------------------------------------------- filtros (uma linha, acima de tudo)
function Filters({
  year,
  month,
  onYear,
  onMonth,
}: {
  year: number;
  month: number | null;
  onYear: (year: number) => void;
  onMonth: (month: number | null) => void;
}) {
  const thisYear = new Date().getFullYear();
  const summary = useYearSummary(year);
  const available = summary.data?.available_years ?? [];
  const years = useMemo(() => [...new Set([thisYear, year, ...available])].sort((a, b) => b - a), [thisYear, year, available]);
  const withData = new Set(summary.data?.months_with_data ?? []);
  const known = summary.data !== undefined && summary.data.year === year;

  return (
    <div className="mb-8 space-y-3">
      <Pills
        label="Ano"
        value={year}
        onChange={(y) => {
          onYear(y);
          onMonth(null);
        }}
        options={years.map((y) => ({ value: y, label: String(y) }))}
      />
      <Pills<number | null>
        label="Mês"
        small
        value={month}
        onChange={onMonth}
        options={[
          { value: null, label: "Ano todo" },
          ...MONTHS_SHORT.map((name, i) => ({
            value: i + 1,
            label: name,
            disabled: known && !withData.has(i + 1),
            title: known && !withData.has(i + 1) ? "Sem transações neste mês" : undefined,
          })),
        ]}
      />
    </div>
  );
}

// ---------------------------------------------------------------- resumo em frase
function periodLabel(d: SpendingResponse): string {
  return d.month ? `Em ${MONTHS_FULL[d.month - 1]} de ${d.year}` : `Em ${d.year}`;
}

function Headline({ d }: { d: SpendingResponse }) {
  const top = d.categories[0];
  const biggest = d.top_transactions[0];
  return (
    <p className="num max-w-[68ch] font-serif text-2xl leading-snug">
      {periodLabel(d)} você gastou {brl(d.expense_total)}
      {d.income_total > 0 && <> de {brl(d.income_total)} que entraram</>}.
      {top && (
        <>
          {" "}
          {top.category} foi o maior gasto, com {pct(top.share, 0)} ({brl(top.total)}).
        </>
      )}
      {biggest && (
        <>
          {" "}
          O maior gasto único foi “{biggest.description}”, {brl(biggest.amount)} em {dayMonth(biggest.date)}.
        </>
      )}
    </p>
  );
}

// ---------------------------------------------------------------- ranking de categorias (clicável)
function CategoryRanking({
  categories,
  active,
  onSelect,
}: {
  categories: SpendCategory[];
  active: string;
  onSelect: (category: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const max = categories[0]?.total ?? 1;
  const visible = showAll ? categories : categories.slice(0, VISIBLE_CATEGORIES);
  return (
    <div>
      <h3 className="text-xl">Para onde foi</h3>
      <p className="mt-1 text-sm text-muted">Clique numa categoria para ver o que há dentro dela.</p>
      <ul className="mt-3">
        {visible.map((c) => {
          const selected = c.category === active;
          return (
            <li key={c.category}>
              <button
                type="button"
                aria-pressed={selected}
                onClick={() => onSelect(c.category)}
                className={`block w-full border-l-[3px] px-3 py-2 text-left ${
                  selected ? "border-ink bg-surface" : "border-transparent hover:bg-surface/70"
                }`}
              >
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate">{c.category}</span>
                  <span className="num shrink-0">
                    {brl(c.total)} <span className="text-muted">{pct(c.share, 0)}</span>
                  </span>
                </span>
                <span className="mt-1.5 block h-2 bg-line/40" aria-hidden>
                  <span
                    className="block h-full rounded-r-[4px]"
                    style={{ width: `${Math.max((c.total / max) * 100, 1.5)}%`, background: SERIES_BLUE }}
                  />
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {categories.length > VISIBLE_CATEGORIES && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="mt-2 px-3 text-sm text-muted underline underline-offset-2 hover:text-ink"
        >
          {showAll ? "Mostrar só as maiores" : `Mostrar todas (${categories.length})`}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- detalhe da categoria
function MonthlyTrend({ category, month, year }: { category: SpendCategory; month: number | null; year: number }) {
  const data = category.monthly.map((value, i) => ({ month: i + 1, label: MONTHS_SHORT[i] ?? "", value }));
  return (
    <ChartOrTable
      label={`${category.category} por mês em ${year}`}
      head={["Mês", "Gasto"]}
      rows={data.map((r) => [`${r.label}/${year}`, brl(r.value)])}
      chart={
        <div className="h-48" role="img" aria-label={`Gasto mensal em ${category.category}, ${year}`}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} interval={0} />
              <YAxis width={56} tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: "rgba(23,33,43,0.06)" }}
                content={(props) => (
                  <TooltipBox
                    {...(props as unknown as TipProps)}
                    title={(l) => `${String(l)}/${year}`}
                    format={(v) => brl(v)}
                  />
                )}
              />
              <Bar dataKey="value" name={category.category} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                {data.map((r) => (
                  <Cell key={r.month} fill={month === null || month === r.month ? SERIES_BLUE : MUTED_BAR} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      }
    />
  );
}

function CategoryDetail({ category, d }: { category: SpendCategory; d: SpendingResponse }) {
  const maxMerchant = category.merchants[0]?.total ?? 1;
  return (
    <div className="min-w-0 border-t border-line pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
      <h3 className="text-xl">{category.category}</h3>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-3">
        <Figure label="No período" value={brl(category.total)} hint={`${pct(category.share, 0)} dos gastos`} />
        <Figure label="Lançamentos" value={category.count} />
        <Figure label="Média por compra" value={brl(category.average)} />
      </dl>

      <h4 className="mt-7 text-base font-medium">Mês a mês em {d.year}</h4>
      <MonthlyTrend category={category} month={d.month} year={d.year} />

      <h4 className="mt-7 text-base font-medium">Onde, dentro dessa categoria</h4>
      <ul className="mt-2 space-y-2">
        {category.merchants.map((m) => (
          <li key={m.name}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="truncate">
                {m.name} <span className="text-muted">· {m.count}x</span>
              </span>
              <span className="num shrink-0">
                {brl(m.total)} <span className="text-muted">{pct(m.share, 0)}</span>
              </span>
            </div>
            <div className="mt-1 h-1.5 bg-line/40" aria-hidden>
              <div className="h-full rounded-r-[4px]" style={{ width: `${Math.max((m.total / maxMerchant) * 100, 2)}%`, background: SERIES_BLUE }} />
            </div>
          </li>
        ))}
      </ul>

      <h4 className="mt-7 text-base font-medium">Maiores lançamentos</h4>
      <ul className="mt-1 divide-y divide-line/60 text-sm">
        {category.top_transactions.map((t) => (
          <li key={t.id} className="flex items-baseline justify-between gap-3 py-1.5">
            <span className="min-w-0 truncate">
              <span className="num text-muted">{dayMonth(t.date)}</span> {t.description}
            </span>
            <span className="num shrink-0">{brl(t.amount)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------- quando você gasta
function WeekdayChart({ d }: { d: SpendingResponse }) {
  const data = d.weekdays.map((w) => ({ ...w, value: w.average_per_day }));
  const peak = data.reduce((best, w) => (w.value > best.value ? w : best), data[0]);
  return (
    <ChartOrTable
      label="Gasto médio por dia da semana"
      head={["Dia", "Média por dia", "Total", "Lançamentos"]}
      rows={data.map((w) => [w.label, brl(w.average_per_day), brl(w.total), w.count])}
      chart={
        <div className="h-48" role="img" aria-label="Gasto médio por dia da semana">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
              <CartesianGrid stroke={GRID} vertical={false} />
              <XAxis dataKey="label" tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <YAxis width={56} tickFormatter={brlCompactFmt} tick={AXIS_TICK} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: "rgba(23,33,43,0.06)" }}
                content={(props) => (
                  <TooltipBox {...(props as unknown as TipProps)} title={(l) => `${String(l)}, em média`} format={(v) => `${brl(v)} por dia`} />
                )}
              />
              <Bar dataKey="value" name="Gasto médio" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                {data.map((w) => (
                  <Cell key={w.weekday} fill={w.weekday === peak?.weekday ? SERIES_BLUE : MUTED_BAR} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      }
    />
  );
}

function WhenSection({ d }: { d: SpendingResponse }) {
  const peakDays = [...d.days].sort((a, b) => b.expense - a.expense).slice(0, 5);
  const peakWeekday = [...d.weekdays].sort((a, b) => b.average_per_day - a.average_per_day)[0];
  return (
    <div className="mt-14 border-t border-line pt-8">
      <h3 className="text-xl">Quando você gasta</h3>
      <p className="mt-1 max-w-[62ch] text-sm text-muted">
        Cada quadrado é um dia; quanto mais escuro, mais você gastou. Passe o mouse para ver o valor.
        {peakWeekday && peakWeekday.average_per_day > 0 && <> O dia da semana mais caro, em média, é {peakWeekday.label}.</>}
      </p>
      <div className="mt-4">
        <SpendCalendar days={d.days} start={d.period_start} end={d.period_end} monthView={d.month !== null} />
      </div>
      <div className="mt-8 grid gap-x-10 gap-y-8 lg:grid-cols-2">
        <div>
          <h4 className="text-base font-medium">Gasto médio por dia da semana</h4>
          <WeekdayChart d={d} />
        </div>
        <div>
          <h4 className="text-base font-medium">Os cinco dias mais caros</h4>
          <ol className="mt-2 divide-y divide-line/60 text-sm">
            {peakDays.map((day, i) => (
              <li key={day.date} className="flex items-baseline justify-between gap-3 py-2">
                <span>
                  <span className="num mr-2 text-muted">{i + 1}.</span>
                  {fullDate(day.date)}
                  <span className="text-muted"> · {day.count} {day.count === 1 ? "lançamento" : "lançamentos"}</span>
                </span>
                <span className="num shrink-0 font-medium">{brl(day.expense)}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- gastos fixos + simulador de corte
function RecurringBlock({
  items,
  onUseAsExtra,
}: {
  items: RecurringExpense[];
  onUseAsExtra: (monthly: number) => void;
}) {
  const [cut, setCut] = useState<Set<string>>(new Set());
  if (items.length === 0) return null;
  const max = items[0]?.monthly_amount ?? 1;
  const total = items.reduce((sum, r) => sum + r.monthly_amount, 0);
  const saved = items.filter((r) => cut.has(r.name)).reduce((sum, r) => sum + r.monthly_amount, 0);

  const toggle = (name: string) =>
    setCut((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  return (
    <div className="mt-14 border-t border-line pt-8">
      <h3 className="text-xl">Gastos que se repetem todo mês</h3>
      <p className="num mt-1 max-w-[68ch] text-sm text-muted">
        São {brl(total)} por mês ({brl(total * 12)} por ano) em contas e assinaturas fixas. Marque o que você cortaria e veja
        quanto sobra.
      </p>
      <ul className="mt-4 grid gap-x-10 md:grid-cols-2">
        {items.map((r) => {
          const checked = cut.has(r.name);
          return (
            <li key={r.name} className="border-b border-line/60 py-2">
              <label className="flex cursor-pointer items-start gap-3">
                <input type="checkbox" checked={checked} onChange={() => toggle(r.name)} className="mt-1 h-4 w-4 accent-[#2a78d6]" />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-3">
                    <span className={`truncate ${checked ? "line-through decoration-muted" : ""}`}>{r.name}</span>
                    <span className="num shrink-0">{brl(r.monthly_amount)}</span>
                  </span>
                  <span className="mt-1 block h-1.5 bg-line/40" aria-hidden>
                    <span
                      className="block h-full rounded-r-[4px]"
                      style={{ width: `${Math.max((r.monthly_amount / max) * 100, 2)}%`, background: checked ? MUTED_BAR : SERIES_BLUE }}
                    />
                  </span>
                  <span className="mt-0.5 block text-xs text-muted">
                    {r.category} · visto em {r.months_seen} meses · {brl(r.yearly_cost)} por ano
                  </span>
                </span>
              </label>
            </li>
          );
        })}
      </ul>
      <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2" aria-live="polite">
        <p className="num">
          {cut.size === 0 ? (
            <span className="text-muted">Nenhum item marcado.</span>
          ) : (
            <>
              Cortando {cut.size} {cut.size === 1 ? "item" : "itens"}: <strong className="font-medium">{brl(saved)}</strong> por mês,{" "}
              <strong className="font-medium">{brl(saved * 12)}</strong> por ano.
            </>
          )}
        </p>
        {saved > 0 && (
          <button
            type="button"
            onClick={() => onUseAsExtra(Math.round(saved))}
            className="rounded-sm bg-ink px-3 py-1.5 text-sm text-surface hover:bg-accent"
          >
            Usar {brl(Math.round(saved))} por mês para quitar dívidas
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- maiores lançamentos
function BiggestTransactions({ d, active }: { d: SpendingResponse; active: string }) {
  const [onlyActive, setOnlyActive] = useState(false);
  const rows = (onlyActive ? d.top_transactions.filter((t) => t.category === active) : d.top_transactions).slice(0, 15);
  return (
    <div className="mt-14 border-t border-line pt-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-xl">Os maiores gastos avulsos</h3>
          <p className="mt-1 text-sm text-muted">Sem contar os gastos fixos listados acima.</p>
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} className="h-4 w-4 accent-[#2a78d6]" />
          Só de {active}
        </label>
      </div>
      <div className="mt-3 overflow-x-auto">
        <table className="num w-full min-w-[640px] text-sm">
          <caption className="sr-only">Maiores gastos individuais do período</caption>
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">Data</th>
              <th scope="col" className="py-2 pr-4 font-medium">Descrição</th>
              <th scope="col" className="py-2 pr-4 font-medium">Categoria</th>
              <th scope="col" className="py-2 pr-4 font-medium">Conta</th>
              <th scope="col" className="py-2 text-right font-medium">Valor</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-b border-line/60">
                <td className="py-2 pr-4 text-muted">{dayMonth(t.date)}</td>
                <td className="py-2 pr-4">{t.description}</td>
                <td className="py-2 pr-4">{t.category}</td>
                <td className="py-2 pr-4 text-muted">{t.account}</td>
                <td className="py-2 text-right font-medium">{brl(t.amount)}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="py-4 text-muted">
                  Nenhum gasto avulso grande nesta categoria.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- seção
function Body({
  d,
  selected,
  onSelect,
  onUseAsExtra,
}: {
  d: SpendingResponse;
  selected: string | null;
  onSelect: (category: string) => void;
  onUseAsExtra: (monthly: number) => void;
}) {
  if (d.categories.length === 0) {
    return (
      <>
        <p className="text-muted">Nenhum gasto encontrado neste período.</p>
        <Notes items={d.warnings} />
      </>
    );
  }
  const active = d.categories.find((c) => c.category === selected) ?? d.categories[0]!;
  return (
    <>
      <Headline d={d} />
      <div className="mt-8">
        <FigureRow>
          <Figure label="Gastou" value={brl(d.expense_total)} hint={`${d.transaction_count} lançamentos`} />
          <Figure label="Recebeu" value={brl(d.income_total)} />
          <Figure
            label={d.savings_total >= 0 ? "Sobrou" : "Faltou"}
            value={brl(Math.abs(d.savings_total))}
            hint={d.income_total > 0 ? `${pct(Math.abs(d.savings_total) / d.income_total, 0)} da renda` : undefined}
          />
          <Figure label="Gastos fixos" value={brl(d.recurring_monthly_total)} hint={`por mês, em ${d.recurring.length} itens`} />
        </FigureRow>
      </div>

      <div className="mt-12 grid gap-y-10 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="lg:pr-8">
          <CategoryRanking categories={d.categories} active={active.category} onSelect={onSelect} />
        </div>
        <CategoryDetail category={active} d={d} />
      </div>

      <Waterfall d={d} />
      <MoneyGraph year={d.year} month={d.month} />
      <CategoryChangeBlock d={d} />
      <ParetoBlock d={d} />
      <StructureBlock year={d.year} />
      <WhenSection d={d} />
      <RecurringBlock items={d.recurring} onUseAsExtra={onUseAsExtra} />
      <BiggestTransactions d={d} active={active.category} />
      <Notes items={d.warnings} />
    </>
  );
}

export function ExplorerSection({ onUseAsExtra }: { onUseAsExtra: (monthly: number) => void }) {
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState<number | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const query = useSpending(year, month);

  return (
    <Section
      id="explorar"
      title="Explorar os gastos"
      intro="Escolha um período e vá abrindo: da categoria ao estabelecimento, do mês ao dia."
    >
      <Filters year={year} month={month} onYear={setYear} onMonth={setMonth} />
      <div aria-busy={query.isFetching} className={query.isPlaceholderData ? "opacity-60" : undefined}>
        <Async query={query}>{(d) => <Body d={d} selected={selected} onSelect={setSelected} onUseAsExtra={onUseAsExtra} />}</Async>
      </div>
    </Section>
  );
}
