const brlFull = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const brlCompact = new Intl.NumberFormat("pt-BR", {
  style: "currency",
  currency: "BRL",
  notation: "compact",
  maximumFractionDigits: 1,
});

export const brl = (value: number | null | undefined): string =>
  value === null || value === undefined ? "—" : brlFull.format(value);

export const brlCompactFmt = (value: number): string => brlCompact.format(value);

/** 0.6217 -> "62,2%" */
export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;
}

export function decimal(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function monthsText(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${decimal(value, 1)} ${value >= 1.95 ? "meses" : "mês"}`;
}

const MONTH_NAMES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"];

/** "2026-04" -> "abr/26" */
export function monthLabel(period: string): string {
  const [year, month] = period.split("-");
  const name = MONTH_NAMES[Number(month) - 1] ?? month;
  return `${name}/${year?.slice(2)}`;
}

export function relativeTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "nunca";
  // O backend grava UTC sem fuso; acrescentamos "Z" para o navegador não assumir horário local.
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`);
  const minutes = Math.round((now.getTime() - date.getTime()) / 60000);
  if (minutes < 1) return "agora há pouco";
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  return `há ${Math.round(hours / 24)} d`;
}

export const MONTHS_SHORT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"] as const;
export const MONTHS_FULL = [
  "janeiro", "fevereiro", "março", "abril", "maio", "junho",
  "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
] as const;

/** "2026-03-08" -> "08/03". Sem `new Date`: evita o deslocamento de fuso em datas só com dia. */
export function dayMonth(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${day}/${month}`;
}

/** "2026-03-08" -> "8 de março de 2026" */
export function fullDate(iso: string): string {
  const [year, month, day] = iso.split("-");
  return `${Number(day)} de ${MONTHS_FULL[Number(month) - 1]} de ${year}`;
}
