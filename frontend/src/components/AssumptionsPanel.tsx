import { useState } from "react";

import type { Assumptions } from "../api/types";

// Espelha MarketAssumptions no backend (mostrado como dica; o servidor é quem aplica o padrão).
const DEFAULTS = { cdi_annual: 13.65, ipca_annual: 4.58, equity_return_annual: 15 } as const;

const FIELDS: { key: keyof Assumptions; label: string }[] = [
  { key: "cdi_annual", label: "CDI (% ao ano)" },
  { key: "ipca_annual", label: "IPCA em 12 meses (%)" },
  { key: "equity_return_annual", label: "Retorno esperado de ações (% ao ano)" },
];

export function AssumptionsPanel({
  value,
  onApply,
}: {
  value: Assumptions;
  onApply: (next: Assumptions) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});

  function apply(event: React.FormEvent) {
    event.preventDefault();
    const next: Assumptions = {};
    for (const { key } of FIELDS) {
      const raw = (draft[key] ?? "").trim().replace(",", ".");
      const parsed = Number(raw);
      if (raw !== "" && Number.isFinite(parsed)) next[key] = parsed / 100;
    }
    onApply(next);
  }

  function reset() {
    setDraft({});
    onApply({});
  }

  const customized = Object.keys(value).length > 0;

  return (
    <details className="group border-y border-line py-3">
      <summary className="cursor-pointer select-none text-sm">
        Premissas de mercado{" "}
        <span className="text-muted">{customized ? "(personalizadas)" : "(padrão: Selic over de 13,65% e IPCA de 4,58%)"}</span>
      </summary>
      <form onSubmit={apply} className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-4">
        {FIELDS.map(({ key, label }) => (
          <label key={key} className="flex flex-col text-sm">
            <span className="text-muted">{label}</span>
            <input
              inputMode="decimal"
              className="num mt-1 w-40 rounded-sm border border-line bg-surface px-2 py-1"
              placeholder={String(DEFAULTS[key]).replace(".", ",")}
              value={draft[key] ?? (value[key] !== undefined ? String((value[key] as number) * 100) : "")}
              onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
            />
          </label>
        ))}
        <button type="submit" className="rounded-sm bg-ink px-4 py-1.5 text-sm text-surface hover:bg-accent">
          Aplicar
        </button>
        <button type="button" onClick={reset} className="rounded-sm border border-ink px-4 py-1.5 text-sm hover:bg-ink hover:text-surface">
          Voltar ao padrão
        </button>
      </form>
      <p className="mt-3 max-w-[70ch] text-xs text-muted">
        Afetam a projeção do patrimônio e a comparação entre dívida e investimento. Confira a Selic atual no site do Banco
        Central antes de decidir algo.
      </p>
    </details>
  );
}
