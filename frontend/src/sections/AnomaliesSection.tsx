import { Bar, BarChart, Cell, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useAnomalies } from "../api/hooks";
import type { AnomaliesResponse, CategoryAnomaly } from "../api/types";
import { Async, Notes, Section } from "../components/ui";
import { brl, decimal, monthLabel } from "../lib/format";

function status(c: CategoryAnomaly): { text: string; className: string } {
  if (c.z_score === null) return { text: "Sem histórico para comparar", className: "text-muted" };
  if (c.is_anomaly) {
    return c.z_score > 0
      ? { text: "Acima do padrão", className: "font-medium text-loss" }
      : { text: "Abaixo do padrão", className: "font-medium text-warn" };
  }
  return { text: "Dentro do padrão", className: "text-gain" };
}

function Body({ d }: { d: AnomaliesResponse }) {
  const scored = d.categories.filter((c) => c.z_score !== null).slice(0, 10);
  const range = Math.max(d.z_threshold + 1, ...scored.map((c) => Math.abs(c.z_score ?? 0)));
  const bound = Math.ceil(range);

  if (d.categories.length === 0) {
    return <p className="text-muted">Nenhuma despesa categorizada neste período.</p>;
  }

  return (
    <>
      {scored.length > 0 && (
        <div
          className="mb-8"
          style={{ height: 56 + scored.length * 34 }}
          role="img"
          aria-label="Z-score do gasto do mês por categoria"
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={scored} layout="vertical" margin={{ top: 4, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--color-line)" strokeDasharray="2 4" horizontal={false} />
              <XAxis
                type="number"
                domain={[-bound, bound]}
                tick={{ fill: "var(--color-muted)", fontSize: 12 }}
                tickLine={false}
              />
              <YAxis
                type="category"
                dataKey="category"
                width={130}
                tick={{ fill: "var(--color-ink)", fontSize: 12 }}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip formatter={(value) => decimal(Number(value), 2)} labelFormatter={(l) => String(l)} />
              <ReferenceLine x={0} stroke="var(--color-ink)" />
              <ReferenceLine x={d.z_threshold} stroke="var(--color-loss)" strokeDasharray="4 3" />
              <ReferenceLine x={-d.z_threshold} stroke="var(--color-loss)" strokeDasharray="4 3" />
              <Bar dataKey="z_score" name="Z-score" isAnimationActive={false} maxBarSize={18}>
                {scored.map((c) => (
                  <Cell key={c.category} fill={c.is_anomaly ? "var(--color-loss)" : "var(--color-muted)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="num w-full min-w-[640px] text-sm">
          <caption className="sr-only">Gasto do mês por categoria comparado à média dos meses anteriores</caption>
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th scope="col" className="py-2 pr-4 font-medium">Categoria</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Até agora</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Projeção do mês</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Média anterior</th>
              <th scope="col" className="py-2 pr-4 text-right font-medium">Z</th>
              <th scope="col" className="py-2 font-medium">Situação</th>
            </tr>
          </thead>
          <tbody>
            {d.categories.map((c) => {
              const s = status(c);
              return (
                <tr key={c.category} className="border-b border-line/60">
                  <th scope="row" className="py-2 pr-4 text-left font-normal">{c.category}</th>
                  <td className="py-2 pr-4 text-right">{brl(c.current_month_to_date)}</td>
                  <td className="py-2 pr-4 text-right">{brl(c.projected_month)}</td>
                  <td className="py-2 pr-4 text-right">{brl(c.baseline_mean)}</td>
                  <td className="py-2 pr-4 text-right">{c.z_score === null ? "—" : decimal(c.z_score, 2)}</td>
                  <td className={`py-2 ${s.className}`}>{s.text}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-sm text-muted">
        Base de comparação: {d.baseline_months.map(monthLabel).join(", ") || "sem meses fechados"}. A projeção
        multiplica o gasto parcial pelos dias do mês; o Z usa a projeção quando já passaram 7 dias.
      </p>
      <Notes items={d.warnings} />
    </>
  );
}

export function AnomaliesSection() {
  const query = useAnomalies();
  return (
    <Section
      id="gastos"
      title="Gastos fora do padrão"
      intro="Quantos desvios padrão o gasto deste mês está da média dos meses anteriores, categoria por categoria. Passou de 2, merece olhar."
    >
      <Async query={query}>{(d) => <Body d={d} />}</Async>
    </Section>
  );
}
