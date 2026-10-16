import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, request, withQuery } from "./client";
import type {
  AnomaliesResponse,
  ArbitrageResponse,
  Assumptions,
  CashForecastResponse,
  CashflowResponse,
  CategoryChangeResponse,
  ChangeMode,
  ConcentrationResponse,
  FlowAuditResponse,
  InstallmentsResponse,
  MoneyGraphResponse,
  NetWorthResponse,
  PaceResponse,
  PortfolioResponse,
  StructureResponse,
  CreditRiskResponse,
  LoanStrategyResponse,
  MonteCarloResponse,
  SpendingResponse,
  SyncResponse,
  SyncStatus,
  YearSummaryResponse,
} from "./types";

/** Erros 4xx não melhoram ao repetir; só tentamos de novo falhas de rede/servidor. */
export const shouldRetry = (count: number, error: Error): boolean =>
  !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 1;

export function useYearSummary(year: number) {
  return useQuery({
    queryKey: ["year-summary", year],
    queryFn: () => request<YearSummaryResponse>(withQuery("/quant/year-summary", { year })),
    placeholderData: keepPreviousData, // ao trocar de ano, mantém o anterior na tela até chegar o novo
    retry: shouldRetry,
  });
}

export function useSpending(year: number, month: number | null) {
  return useQuery({
    queryKey: ["spending", year, month],
    queryFn: () => request<SpendingResponse>(withQuery("/quant/spending", { year, month: month ?? undefined })),
    placeholderData: keepPreviousData, // trocar período mantém o gráfico anterior até o novo chegar
    retry: shouldRetry,
  });
}

export function useCashflow() {
  return useQuery({
    queryKey: ["cashflow"],
    queryFn: () => request<CashflowResponse>("/quant/cashflow"),
    retry: shouldRetry,
  });
}

export function useAnomalies() {
  return useQuery({
    queryKey: ["anomalies"],
    queryFn: () => request<AnomaliesResponse>("/quant/anomalies"),
    retry: shouldRetry,
  });
}

export function useMonteCarlo(assumptions: Assumptions) {
  return useQuery({
    queryKey: ["monte-carlo", assumptions],
    // seed fixa: o leque não "treme" a cada recarregamento da página.
    queryFn: () => request<MonteCarloResponse>(withQuery("/quant/monte-carlo", { ...assumptions, seed: 42 })),
    retry: shouldRetry,
  });
}

export function useArbitrage(assumptions: Assumptions) {
  return useQuery({
    queryKey: ["arbitrage", assumptions],
    queryFn: () => request<ArbitrageResponse>(withQuery("/quant/arbitrage", { ...assumptions })),
    retry: shouldRetry,
  });
}

export function useLoanStrategy(extra: number) {
  return useQuery({
    queryKey: ["loan-strategy", extra],
    queryFn: () => request<LoanStrategyResponse>(withQuery("/quant/loan-strategy", { extra })),
    placeholderData: keepPreviousData,
    retry: shouldRetry,
  });
}

export function useCreditRisk() {
  return useQuery({
    queryKey: ["credit-risk"],
    queryFn: () => request<CreditRiskResponse>("/quant/credit-risk"),
    retry: shouldRetry,
  });
}

export function useSyncStatus() {
  return useQuery({
    queryKey: ["sync-status"],
    queryFn: () => request<SyncStatus>("/sync/status"),
    retry: shouldRetry,
  });
}

export function useSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => request<SyncResponse>("/sync", { method: "POST" }),
    onSuccess: () => client.invalidateQueries(),
  });
}

// ---------------------------------------------------------------- análises extras
const keep = { placeholderData: keepPreviousData, retry: shouldRetry } as const;

export function useCashForecast() {
  return useQuery({ queryKey: ["cash-forecast"], queryFn: () => request<CashForecastResponse>("/quant/cash-forecast"), retry: shouldRetry });
}

export function usePace() {
  return useQuery({ queryKey: ["pace"], queryFn: () => request<PaceResponse>("/quant/pace"), retry: shouldRetry });
}

export function useInstallments() {
  return useQuery({ queryKey: ["installments"], queryFn: () => request<InstallmentsResponse>("/quant/installments"), retry: shouldRetry });
}

export function useCategoryChange(year: number, month: number, mode: ChangeMode) {
  return useQuery({
    queryKey: ["category-change", year, month, mode],
    queryFn: () => request<CategoryChangeResponse>(withQuery("/quant/category-change", { year, month, mode })),
    ...keep,
  });
}

export function useStructure(year: number) {
  return useQuery({
    queryKey: ["structure", year],
    queryFn: () => request<StructureResponse>(withQuery("/quant/structure", { year })),
    ...keep,
  });
}

export function useConcentration(year: number, month: number | null) {
  return useQuery({
    queryKey: ["concentration", year, month],
    queryFn: () => request<ConcentrationResponse>(withQuery("/quant/concentration", { year, month: month ?? undefined })),
    ...keep,
  });
}

export function useMoneyGraph(year: number, month: number | null) {
  return useQuery({
    queryKey: ["money-graph", year, month],
    queryFn: () => request<MoneyGraphResponse>(withQuery("/quant/money-graph", { year, month: month ?? undefined })),
    ...keep,
  });
}

export function usePortfolio(assumptions: Assumptions) {
  return useQuery({
    queryKey: ["portfolio", assumptions],
    queryFn: () => request<PortfolioResponse>(withQuery("/quant/portfolio", { ...assumptions })),
    ...keep,
  });
}

export function useNetWorth() {
  return useQuery({ queryKey: ["net-worth"], queryFn: () => request<NetWorthResponse>("/quant/net-worth"), retry: shouldRetry });
}

export function useFlowAudit(year: number) {
  return useQuery({
    queryKey: ["flow-audit", year],
    queryFn: () => request<FlowAuditResponse>(withQuery("/quant/flow-audit", { year })),
    ...keep,
  });
}
