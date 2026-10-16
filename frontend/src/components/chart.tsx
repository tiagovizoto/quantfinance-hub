import { useState } from "react";
import type { ReactNode } from "react";

/** Forma mínima do que o Recharts entrega ao `content` de um Tooltip. */
export interface TipProps {
  active?: boolean;
  label?: string | number;
  payload?: ReadonlyArray<{ name?: string | number; value?: unknown; color?: string; stroke?: string; fill?: string }>;
}

/** Tooltip padrão: o valor lidera (forte), o nome vem depois (secundário), cada linha com uma chave em traço. */
export function TooltipBox({
  active,
  label,
  payload,
  title,
  format,
}: TipProps & { title?: (label: string | number | undefined) => ReactNode; format: (value: number, name: string) => string }) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="pointer-events-none border border-line bg-surface px-3 py-2 text-sm shadow-sm">
      <p className="mb-1 text-muted">{title ? title(label) : label}</p>
      <ul className="space-y-0.5">
        {payload.map((item, index) => {
          const value = Number(item.value);
          if (!Number.isFinite(value)) return null;
          const color = item.color ?? item.stroke ?? item.fill ?? "#566370";
          return (
            <li key={`${String(item.name)}-${index}`} className="flex items-baseline gap-2">
              <span aria-hidden className="inline-block h-0.5 w-3 shrink-0 self-center" style={{ background: color }} />
              <span className="num font-medium text-ink">{format(value, String(item.name ?? ""))}</span>
              {item.name !== undefined && item.name !== "" && <span className="text-muted">{String(item.name)}</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Um gráfico e a sua tabela equivalente (os mesmos números, sem depender de cor nem de hover). */
export function ChartOrTable({
  label,
  chart,
  head,
  rows,
}: {
  label: string;
  chart: ReactNode;
  head: string[];
  rows: (string | number)[][];
}) {
  const [asTable, setAsTable] = useState(false);
  return (
    <div>
      <div className="mb-1 flex justify-end">
        <button
          type="button"
          aria-pressed={asTable}
          onClick={() => setAsTable((v) => !v)}
          className="rounded-sm px-2 py-0.5 text-xs text-muted underline-offset-2 hover:text-ink hover:underline"
        >
          {asTable ? "Ver gráfico" : "Ver como tabela"}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-72 overflow-auto">
          <table className="num w-full text-sm">
            <caption className="sr-only">{label}</caption>
            <thead>
              <tr className="border-b border-line text-left text-muted">
                {head.map((h, i) => (
                  <th key={h} scope="col" className={`py-1.5 pr-4 font-medium ${i > 0 ? "text-right" : ""}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={String(row[0])} className="border-b border-line/60">
                  {row.map((cell, i) => (
                    <td key={`${String(row[0])}-${i}`} className={`py-1.5 pr-4 ${i > 0 ? "text-right" : ""}`}>
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        chart
      )}
    </div>
  );
}

export interface PillOption<T> {
  value: T;
  label: string;
  disabled?: boolean;
  title?: string;
}

/** Filtro em linha (ano, mês, estratégia...). Uma opção ativa; as sem dados ficam desabilitadas. */
export function Pills<T extends string | number | null>({
  label,
  options,
  value,
  onChange,
  small = false,
}: {
  label: string;
  options: PillOption<T>[];
  value: T;
  onChange: (value: T) => void;
  small?: boolean;
}) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={String(option.value)}
            type="button"
            aria-pressed={active}
            disabled={option.disabled}
            title={option.title}
            onClick={() => onChange(option.value)}
            className={`num rounded-sm border ${small ? "px-2 py-0.5 text-xs" : "px-3 py-1 text-sm"} ${
              active ? "border-ink bg-ink text-surface" : "border-line hover:border-ink"
            } disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-line`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/** Legenda de identidade (>= 2 séries): amostra na forma da marca + nome em tinta de texto. */
export function Legend({ items, shape = "square" }: { items: { label: string; color: string }[]; shape?: "square" | "line" }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5">
          <span
            aria-hidden
            className={shape === "line" ? "inline-block h-0.5 w-4" : "inline-block h-2.5 w-2.5 rounded-[2px]"}
            style={{ background: item.color }}
          />
          {item.label}
        </li>
      ))}
    </ul>
  );
}
