import type { UseQueryResult } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "../api/client";

export function Section({
  id,
  title,
  intro,
  children,
}: {
  id: string;
  title: string;
  intro?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-20 border-t border-ink pt-6 pb-14">
      <h2 id={`${id}-title`} className="text-[1.75rem] leading-tight">
        {title}
      </h2>
      {intro && <p className="mt-1 max-w-[62ch] text-muted">{intro}</p>}
      <div className="mt-6">{children}</div>
    </section>
  );
}

export function Figure({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="num mt-0.5 font-serif text-[1.7rem] leading-none">{value}</dd>
      {hint && <dd className="num mt-1.5 text-sm text-muted">{hint}</dd>}
    </div>
  );
}

export function FigureRow({ children }: { children: ReactNode }) {
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-6 md:grid-cols-4 md:divide-x md:divide-line [&>div]:md:pl-6 [&>div:first-child]:md:pl-0">
      {children}
    </dl>
  );
}

export function Skeleton() {
  return (
    <div role="status" aria-label="Carregando" className="space-y-3">
      <div className="h-6 w-1/3 rounded-sm bg-line/70 motion-safe:animate-pulse" />
      <div className="h-56 rounded-sm bg-line/50 motion-safe:animate-pulse" />
    </div>
  );
}

export function ErrorBox({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const lackData = error instanceof ApiError && error.status === 422;
  return (
    <div role="alert" className="border-l-2 border-loss bg-surface px-4 py-3">
      <p className="font-medium">{lackData ? "Ainda não há dados suficientes para esta análise." : "Não foi possível carregar esta seção."}</p>
      <p className="mt-1 text-sm text-muted">{error.message}</p>
      {lackData && (
        <p className="mt-1 text-sm text-muted">Use “Sincronizar agora” para importar o histórico do MeuPluggy.</p>
      )}
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-sm border border-ink px-3 py-1 text-sm hover:bg-ink hover:text-surface"
      >
        Tentar de novo
      </button>
    </div>
  );
}

export function Async<T>({
  query,
  children,
}: {
  query: UseQueryResult<T, Error>;
  children: (data: T) => ReactNode;
}) {
  if (query.isPending) return <Skeleton />;
  if (query.isError) return <ErrorBox error={query.error} onRetry={() => void query.refetch()} />;
  return <>{children(query.data)}</>;
}

export function Notes({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="mt-5 space-y-1 text-sm text-muted">
      {items.map((text) => (
        <li key={text} className="max-w-[80ch]">
          {text}
        </li>
      ))}
    </ul>
  );
}

/** Barra horizontal com marca de limite. `value` e `max` na mesma unidade. */
export function Meter({
  label,
  valueText,
  value,
  max,
  threshold,
  thresholdText,
  higherIsWorse = true,
}: {
  label: string;
  valueText: string;
  value: number | null;
  max: number;
  threshold: number;
  thresholdText: string;
  higherIsWorse?: boolean;
}) {
  const ratio = value === null ? 0 : Math.min(Math.max(value / max, 0), 1);
  const bad = value !== null && (higherIsWorse ? value >= threshold : value < threshold);
  return (
    <div>
      <div className="flex items-baseline justify-between gap-4">
        <span className="text-sm">{label}</span>
        <span className="num font-serif text-xl">{valueText}</span>
      </div>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value ?? 0}
        aria-valuetext={valueText}
        className="relative mt-1.5 h-2 bg-line/70"
      >
        <div className={`h-full ${bad ? "bg-loss" : "bg-gain"}`} style={{ width: `${ratio * 100}%` }} />
        <div
          className="absolute -top-1 h-4 w-px bg-ink"
          style={{ left: `${Math.min(threshold / max, 1) * 100}%` }}
          aria-hidden
        />
      </div>
      <p className="mt-1 text-xs text-muted">{thresholdText}</p>
    </div>
  );
}
