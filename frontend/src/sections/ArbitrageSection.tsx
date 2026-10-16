import { useArbitrage } from "../api/hooks";
import type { ArbitrageResponse, ArbitrageRow, LoanArbitrage } from "../api/types";
import { Async, Notes, Section } from "../components/ui";
import { brl, pct } from "../lib/format";

function Decision({ row }: { row: ArbitrageRow }) {
  return row.decision === "AMORTIZAR_DIVIDA" ? (
    <span className="font-medium text-loss">Amortizar a dívida</span>
  ) : (
    <span className="font-medium text-gain">Manter investido</span>
  );
}

function LoanBlock({ loan }: { loan: LoanArbitrage }) {
  const source = loan.rows.find((r) => r.investment_id === loan.suggested_source_to_liquidate);
  return (
    <article className="mb-10 last:mb-0">
      <h3 className="text-xl">{loan.loan_name ?? "Empréstimo"}</h3>
      <p className="num text-sm text-muted">
        Saldo devedor {brl(loan.outstanding_balance)}, CET de {pct(loan.cet_annual, 2)} ao ano, {loan.horizon_months}{" "}
        meses restantes.
      </p>
      {source && (
        <p className="mt-2 text-sm">
          Se for amortizar, comece resgatando <strong className="font-medium">{source.investment_name ?? source.investment_id}</strong>:
          é onde o dinheiro rende menos que a dívida custa.
        </p>
      )}
      {loan.rows.length === 0 ? (
        <p className="mt-3 text-muted">Sem investimentos de renda fixa para comparar.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="num w-full min-w-[760px] text-sm">
            <caption className="sr-only">Comparação entre amortizar {loan.loan_name} e manter cada investimento</caption>
            <thead>
              <tr className="border-b border-line text-left text-muted">
                <th scope="col" className="py-2 pr-4 font-medium">Investimento</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Rende líquido (a.a.)</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Se amortizar</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Se manter</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Vantagem de manter (VPL)</th>
                <th scope="col" className="py-2 font-medium">Melhor opção</th>
              </tr>
            </thead>
            <tbody>
              {loan.rows.map((r) => (
                <tr key={r.investment_id} className="border-b border-line/60 align-top">
                  <th scope="row" className="py-2 pr-4 text-left font-normal">
                    {r.investment_name ?? r.investment_id}
                    {r.subtype && <span className="block text-xs text-muted">{r.subtype}</span>}
                    {r.notes.map((n) => (
                      <span key={n} className="block max-w-[34ch] text-xs text-warn">{n}</span>
                    ))}
                  </th>
                  <td className="py-2 pr-4 text-right">
                    {pct(r.net_annual_return_if_kept, 2)}
                    <span className="block text-xs text-muted">dívida custa {pct(r.cet_annual, 2)}</span>
                  </td>
                  <td className="py-2 pr-4 text-right">{brl(r.value_if_amortize)}</td>
                  <td className="py-2 pr-4 text-right">{brl(r.value_if_keep)}</td>
                  <td className={`py-2 pr-4 text-right ${r.vpl_advantage_keep >= 0 ? "text-gain" : "text-loss"}`}>
                    {brl(r.vpl_advantage_keep)}
                  </td>
                  <td className="py-2"><Decision row={r} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </article>
  );
}

function Body({ d }: { d: ArbitrageResponse }) {
  return (
    <>
      {d.loans.length === 0 ? (
        <p className="text-muted">Nenhum empréstimo ou financiamento ativo com CET informado.</p>
      ) : (
        d.loans.map((loan) => <LoanBlock key={loan.loan_id} loan={loan} />)
      )}
      <p className="mt-6 max-w-[80ch] text-sm text-muted">
        Os valores são o que sobra no fim do prazo da dívida em cada caminho, já descontado o IR da tabela regressiva
        (LCI e LCA são isentas). O VPL usa {pct(d.discount_rate_annual, 2)} ao ano, o CDI líquido de IR. Não considera
        carência, liquidez nem marcação a mercado do título.
      </p>
      <Notes items={d.warnings} />
    </>
  );
}

export function ArbitrageSection({ assumptions }: { assumptions: Parameters<typeof useArbitrage>[0] }) {
  const query = useArbitrage(assumptions);
  return (
    <Section
      id="divida"
      title="Dívida ou investimento"
      intro="Para cada financiamento: vale mais usar o dinheiro aplicado para quitar, ou deixar rendendo?"
    >
      <Async query={query}>{(d) => <Body d={d} />}</Async>
    </Section>
  );
}
