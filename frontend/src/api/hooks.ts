import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, request, withQuery } from "./client";
import type {
  AnomaliesResponse,
  ArbitrageResponse,
  Assumptions,
  CashflowResponse,
  CreditRiskResponse,
  LoanStrategyResponse,
  MonteCarloResponse,
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
