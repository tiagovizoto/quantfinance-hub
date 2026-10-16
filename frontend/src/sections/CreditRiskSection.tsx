import { useCreditRisk } from "../api/hooks";
import type { CreditRiskResponse } from "../api/types";
import { Async, Meter, Notes, Section } from "../components/ui";
import { brl, decimal, pct } from "../lib/format";

function Body({ d }: { d: CreditRiskResponse }) {
  return (
    <>
      {d.flags.length > 0 && (
        <div className="mb-8 border-l-2 border-warn bg-surface px-4 py-3" role="status">
          <p className="font-medium">Pontos de atenção</p>
          <ul className="mt-1 list-disc pl-5 text-sm">
            {d.flags.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid gap-x-12 gap-y-8 md:grid-cols-2">
        <Meter
          label="Uso do limite dos cartões"
          valueText={pct(d.utilization_total)}
          value={d.utilization_total}
          max={1}
          threshold={0.3}
          thresholdText={`${brl(d.credit_used_total)} de ${brl(d.credit_limit_total)}. Acima de 30% costuma pesar no score.`}
        />
        <Meter
          label="Cobertura da dívida"
          valueText={d.debt_coverage_ratio === null ? "—" : `${decimal(d.debt_coverage_ratio, 2)}×`}
          value={d.debt_coverage_ratio}
          max={2}
          threshold={1}
          higherIsWorse={false}
          thresholdText={`Liquidez de ${brl(d.immediate_liquidity)} para ${brl(d.total_debt)} de dívida. Abaixo de 1× não dá para quitar tudo.`}
        />
        <Meter
          label="Renda comprometida com parcelas"
          valueText={pct(d.income_commitment)}
          value={d.income_commitment}
          max={0.6}
          threshold={0.3}
          thresholdText={`Parcelas estimadas de ${brl(d.monthly_installments_estimated)} por mês sobre renda média de ${brl(d.avg_monthly_income)}.`}
        />
      </div>

      {d.cards.length > 0 && (
        <div className="mt-10">
          <h3 className="text-xl">Por cartão</h3>
          <div className="mt-3 grid gap-x-12 gap-y-6 md:grid-cols-2">
            {d.cards.map((c) => (
              <Meter
                key={c.account_id}
                label={c.name}
                valueText={pct(c.utilization)}
                value={c.utilization}
                max={1}
                threshold={0.3}
                thresholdText={`${brl(c.used)} de ${brl(c.credit_limit)}`}
              />
            ))}
          </div>
        </div>
      )}
      <Notes items={d.warnings} />
    </>
  );
}

export function CreditRiskSection() {
  const query = useCreditRisk();
  return (
    <Section
      id="risco"
      title="Risco de crédito"
      intro="Quanto do crédito disponível você está usando e se a liquidez cobre o que você deve."
    >
      <Async query={query}>{(d) => <Body d={d} />}</Async>
    </Section>
  );
}
