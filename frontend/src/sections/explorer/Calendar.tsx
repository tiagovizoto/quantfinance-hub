import { useMemo, useState } from "react";

import type { SpendDay } from "../../api/types";
import { NO_DATA, SEQUENTIAL, bucketColor, quantileBuckets } from "../../lib/chart";
import { MONTHS_SHORT, brl, fullDate } from "../../lib/format";

const WEEKDAY_LABELS = ["Seg", "", "Qua", "", "Sex", "", "Dom"]; // segunda no topo, como o backend (0 = segunda)
const DAY_MS = 86_400_000;

interface Cell {
  iso: string;
  col: number;
  row: number;
  day: number;
  month: number;
}

const toIso = (d: Date): string => d.toISOString().slice(0, 10);
/** Datas só com dia: trabalhamos em UTC para o horário de verão/fuso nunca mudar o dia. */
const parseIso = (iso: string): Date => new Date(`${iso}T00:00:00Z`);

function buildCells(start: string, end: string): { cells: Cell[]; cols: number } {
  const first = parseIso(start);
  const last = parseIso(end);
  const mondayOffset = (first.getUTCDay() + 6) % 7; // 0 = segunda
  const gridStart = first.getTime() - mondayOffset * DAY_MS;
  const cells: Cell[] = [];
  for (let t = first.getTime(); t <= last.getTime(); t += DAY_MS) {
    const date = new Date(t);
    const index = Math.round((t - gridStart) / DAY_MS);
    cells.push({
      iso: toIso(date),
      col: Math.floor(index / 7),
      row: index % 7,
      day: date.getUTCDate(),
      month: date.getUTCMonth(),
    });
  }
  return { cells, cols: (cells.at(-1)?.col ?? 0) + 1 };
}

/** Calendário de gasto diário: cada quadrado é um dia, mais escuro = mais gasto. */
export function SpendCalendar({
  days,
  start,
  end,
  monthView,
}: {
  days: SpendDay[];
  start: string;
  end: string;
  monthView: boolean;
}) {
  const [hover, setHover] = useState<{ cell: Cell; x: number; y: number } | null>(null);
  const byDay = useMemo(() => new Map(days.map((d) => [d.date, d])), [days]);
  const { cells, cols } = useMemo(() => buildCells(start, end), [start, end]);
  const thresholds = useMemo(() => quantileBuckets(days.map((d) => d.expense)), [days]);

  const size = monthView ? 46 : 14;
  const gap = monthView ? 4 : 3;
  const pitch = size + gap;
  const left = 30;
  const top = monthView ? 4 : 18;
  const width = left + cols * pitch;
  const height = top + 7 * pitch;
  const peak = days.reduce((best, d) => (d.expense > best.expense ? d : best), days[0] ?? { date: "", expense: 0, count: 0 });

  // Rótulo do mês acima da primeira coluna em que ele aparece (só na visão do ano).
  const monthLabels: { col: number; label: string }[] = [];
  if (!monthView) {
    let current = -1;
    for (const cell of cells) {
      if (cell.month !== current && cell.day <= 7) {
        current = cell.month;
        monthLabels.push({ col: cell.col, label: MONTHS_SHORT[cell.month] ?? "" });
      }
    }
  }

  const hovered = hover ? byDay.get(hover.cell.iso) : undefined;

  return (
    <div>
      <div className="relative overflow-x-auto pb-1">
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={`Calendário de gastos diários. ${peak.expense > 0 ? `Dia de maior gasto: ${fullDate(peak.date)}, ${brl(peak.expense)}.` : ""}`}
          className="block max-w-none"
          onPointerLeave={() => setHover(null)}
        >
          {WEEKDAY_LABELS.map((label, row) => (
            <text key={row} x={0} y={top + row * pitch + size * 0.72} fontSize={11} fill="#566370">
              {label}
            </text>
          ))}
          {monthLabels.map(({ col, label }) => (
            <text key={`${col}-${label}`} x={left + col * pitch} y={11} fontSize={11} fill="#566370">
              {label}
            </text>
          ))}
          {cells.map((cell) => {
            const day = byDay.get(cell.iso);
            const value = day?.expense ?? 0;
            const fill = bucketColor(value, thresholds);
            const x = left + cell.col * pitch;
            const y = top + cell.row * pitch;
            const dark = fill !== NO_DATA && SEQUENTIAL.indexOf(fill as (typeof SEQUENTIAL)[number]) >= 2;
            return (
              <g
                key={cell.iso}
                onPointerMove={(e) => {
                  const box = e.currentTarget.ownerSVGElement?.getBoundingClientRect();
                  if (box) setHover({ cell, x: e.clientX - box.left, y: e.clientY - box.top });
                }}
              >
                {/* alvo maior que a marca: inclui o espaço entre quadrados */}
                <rect x={x - gap / 2} y={y - gap / 2} width={pitch} height={pitch} fill="transparent" />
                <rect
                  x={x}
                  y={y}
                  width={size}
                  height={size}
                  rx={monthView ? 4 : 2}
                  fill={fill}
                  stroke={hover?.cell.iso === cell.iso ? "#17212b" : "none"}
                  strokeWidth={1.5}
                />
                {monthView && (
                  <text x={x + 6} y={y + 16} fontSize={12} fill={dark ? "#ffffff" : "#17212b"}>
                    {cell.day}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute z-10 border border-line bg-surface px-3 py-2 text-sm shadow-sm"
            style={{ left: Math.min(hover.x + 12, Math.max(width - 190, 0)), top: hover.y + 14 }}
          >
            <p className="text-muted">{fullDate(hover.cell.iso)}</p>
            {hovered ? (
              <p>
                <span className="num font-medium">{brl(hovered.expense)}</span>{" "}
                <span className="text-muted">em {hovered.count} {hovered.count === 1 ? "lançamento" : "lançamentos"}</span>
              </p>
            ) : (
              <p className="text-muted">Sem gastos</p>
            )}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        <span className="flex items-center gap-1.5">
          <span aria-hidden className="inline-block h-3 w-3 rounded-[2px]" style={{ background: NO_DATA }} />
          sem gasto
        </span>
        <span className="flex items-center gap-1">
          menos
          {SEQUENTIAL.map((color) => (
            <span key={color} aria-hidden className="inline-block h-3 w-3 rounded-[2px]" style={{ background: color }} />
          ))}
          mais
        </span>
      </div>
    </div>
  );
}
