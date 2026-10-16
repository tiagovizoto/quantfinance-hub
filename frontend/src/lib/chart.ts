/** Parâmetros visuais dos gráficos (paleta categórica validada com validate_palette.js, modo claro). */

/** Identidade: slots na ordem fixa. Delta E adjacente (CVD) >= 9,1; três slots ficam abaixo de 3:1
 * de contraste, então sempre há legenda, nome visível ou tabela ao lado. */
export const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#6250d6"] as const;
export const SERIES_BLUE = SERIES[0];
export const SERIES_ORANGE = SERIES[1];

/** Entidades diferentes, cores diferentes: empréstimos usam os slots 1-4; estratégias, 5-6;
 * principal x juros têm par próprio (neutro x "perda"), para nenhuma cor mudar de significado na mesma tela. */
export const STRATEGY_COLOR = { baseline: "#566370", avalanche: SERIES[5], snowball: SERIES[4] } as const;
export const PRINCIPAL_COLOR = "#7d8fa1";
export const INTEREST_COLOR = "#b4372b";

/** Magnitude: uma matiz (azul), claro -> escuro. O mais claro nunca passa do passo 250 (2:1 na superfície). */
export const SEQUENTIAL = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#0d366b"] as const;

export const GRID = "#dde3e9"; // hairline sólida, um tom acima da superfície
export const NO_DATA = "#e6ebf0";
export const MUTED_BAR = "#c6cfd8"; // contexto na forma "ênfase": destaca um, esmaece o resto

export const AXIS_TICK = { fill: "#566370", fontSize: 12 } as const;

/** Sorteia o passo da escala sequencial por quantis dos valores > 0 (um outlier não achata o resto). */
export function quantileBuckets(values: number[], steps = SEQUENTIAL.length): number[] {
  const sorted = values.filter((v) => v > 0).sort((a, b) => a - b);
  if (sorted.length === 0) return [];
  return Array.from({ length: steps - 1 }, (_, i) => {
    const q = (i + 1) / steps;
    return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))] ?? 0;
  });
}

export function bucketColor(value: number, thresholds: number[]): string {
  if (value <= 0) return NO_DATA;
  const index = thresholds.filter((t) => value > t).length;
  return SEQUENTIAL[Math.min(index, SEQUENTIAL.length - 1)] ?? SEQUENTIAL[0];
}
