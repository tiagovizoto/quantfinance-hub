// Espelha backend/app/schemas/quant.py e sync.py

export interface MonthlyFlow {
  month: string;
  income: number;
  expense: number;
  savings: number;
  savings_rate: number | null;
}

export interface CashflowResponse {
  as_of: string;
  window_months: number;
  months_used: string[];
  monthly: MonthlyFlow[];
  current_month_to_date: MonthlyFlow;
  savings_rate_window: number | null;
  burn_rate_monthly: number | null;
  burn_rate_daily: number | null;
  expense_std_monthly: number | null;
  burn_rate_stress_monthly: number | null;
  immediate_liquidity: number;
  runway_months: number | null;
  runway_stress_months: number | null;
  warnings: string[];
}

export interface YearMonth {
  month: number;
  income: number;
  expense: number;
  savings: number;
  has_data: boolean;
}

export interface YearCategory {
  category: string;
  total: number;
  share: number | null;
  monthly: number[];
}

export interface YearSummaryResponse {
  year: number;
  available_years: number[];
  is_partial: boolean;
  months_with_data: number[];
  income_total: number;
  expense_total: number;
  savings_total: number;
  savings_rate: number | null;
  months: YearMonth[];
  categories: YearCategory[];
  warnings: string[];
}

export interface CategoryAnomaly {
  category: string;
  current_month_to_date: number;
  projected_month: number;
  baseline_mean: number;
  baseline_std: number | null;
  z_score: number | null;
  z_basis: "projected" | "month_to_date";
  p_value_upper: number | null;
  is_anomaly: boolean;
}

export interface AnomaliesResponse {
  as_of: string;
  baseline_months: string[];
  z_threshold: number;
  categories: CategoryAnomaly[];
  warnings: string[];
}

export interface WealthHorizon {
  months: number;
  p10: number;
  p50: number;
  p90: number;
  mean: number;
  prob_negative: number;
}

export interface FanPoint {
  months: number;
  p10: number;
  p50: number;
  p90: number;
}

export interface MonteCarloInputs {
  initial_net_worth: number;
  portfolio_value: number;
  cash: number;
  loans_outstanding: number;
  card_debt: number;
  savings_mu_monthly: number;
  savings_sigma_monthly: number;
  savings_months_used: number;
  portfolio_return_annual_net: number;
  portfolio_vol_annual: number;
}

export interface MonteCarloResponse {
  as_of: string;
  n_sims: number;
  seed: number | null;
  inputs: MonteCarloInputs;
  horizons: WealthHorizon[];
  fan: FanPoint[];
  warnings: string[];
}

export interface ArbitrageRow {
  investment_id: string;
  investment_name: string | null;
  subtype: string | null;
  gross_annual_return: number;
  net_annual_return_if_kept: number;
  cet_annual: number;
  spread_net_vs_cet: number;
  capital_considered: number;
  value_if_amortize: number;
  value_if_keep: number;
  vpl_advantage_keep: number;
  decision: "MANTER_INVESTIDO" | "AMORTIZAR_DIVIDA";
  notes: string[];
}

export interface LoanArbitrage {
  loan_id: string;
  loan_name: string | null;
  outstanding_balance: number;
  cet_annual: number;
  horizon_months: number;
  suggested_source_to_liquidate: string | null;
  rows: ArbitrageRow[];
}

export interface ArbitrageResponse {
  as_of: string;
  discount_rate_annual: number;
  loans: LoanArbitrage[];
  warnings: string[];
}

export interface LoanPlan {
  loan_id: string;
  name: string;
  balance: number;
  cet_annual: number | null;
  amortization: "PRICE" | "SAC";
  remaining_installments: number;
  installment_first: number;
  installment_last: number;
  interest_remaining: number;
  ends_in_months: number | null;
  ends_on: string | null;
}

export interface PayoffStep {
  loan_id: string;
  name: string;
  month: number;
  on: string;
}

export interface StrategyResult {
  key: "baseline" | "avalanche" | "snowball";
  label: string;
  months_to_debt_free: number | null;
  debt_free_on: string | null;
  total_interest: number;
  interest_saved: number;
  months_saved: number;
  payoff_order: PayoffStep[];
}

export interface DebtPoint {
  month: number;
  on: string;
  baseline: number;
  avalanche: number;
  snowball: number;
}

export interface CostPoint {
  month: number;
  on: string;
  baseline_interest: number;
  baseline_principal: number;
  avalanche_interest: number;
  avalanche_principal: number;
  snowball_interest: number;
  snowball_principal: number;
}

export interface TimelinePoint {
  month: number;
  on: string;
  total: number;
  by_loan: number[];
  commitment: number | null;
}

export interface LoanStrategyResponse {
  as_of: string;
  extra_monthly: number;
  avg_monthly_income: number | null;
  total_balance: number;
  total_installments_now: number;
  loans: LoanPlan[];
  strategies: StrategyResult[];
  best_strategy: "avalanche" | "snowball" | null;
  debt_curve: DebtPoint[];
  cost_curve: CostPoint[];
  timeline: TimelinePoint[];
  warnings: string[];
}

export interface SpendMerchant {
  name: string;
  total: number;
  count: number;
  share: number | null;
}

export interface SpendTransaction {
  id: string;
  date: string; // AAAA-MM-DD
  description: string;
  category: string;
  account: string;
  amount: number;
}

export interface SpendCategory {
  category: string;
  total: number;
  share: number | null;
  count: number;
  average: number;
  monthly: number[]; // 12 valores do ano inteiro
  merchants: SpendMerchant[];
  top_transactions: SpendTransaction[];
}

export interface SpendDay {
  date: string;
  expense: number;
  count: number;
}

export interface SpendWeekday {
  weekday: number; // 0 = segunda
  label: string;
  total: number;
  count: number;
  average_per_day: number;
}

export interface RecurringExpense {
  name: string;
  category: string;
  monthly_amount: number;
  yearly_cost: number;
  months_seen: number;
  last_seen: string;
}

export interface SpendingResponse {
  year: number;
  month: number | null;
  available_years: number[];
  period_start: string;
  period_end: string;
  income_total: number;
  expense_total: number;
  savings_total: number;
  transaction_count: number;
  categories: SpendCategory[];
  top_merchants: SpendMerchant[];
  top_transactions: SpendTransaction[];
  days: SpendDay[];
  weekdays: SpendWeekday[];
  recurring: RecurringExpense[];
  recurring_monthly_total: number;
  warnings: string[];
}

export interface CardUtilization {
  account_id: string;
  name: string;
  credit_limit: number;
  used: number;
  utilization: number | null;
}

export interface CreditRiskResponse {
  as_of: string;
  credit_limit_total: number;
  credit_used_total: number;
  utilization_total: number | null;
  cards: CardUtilization[];
  immediate_liquidity: number;
  loans_outstanding: number;
  total_debt: number;
  debt_coverage_ratio: number | null;
  monthly_installments_estimated: number;
  avg_monthly_income: number | null;
  income_commitment: number | null;
  flags: string[];
  warnings: string[];
}

export interface SyncResponse {
  item_id: string;
  accounts: number;
  transactions: number;
  bills: number;
  investments: number;
  loans: number;
  warnings: string[];
  started_at: string;
  finished_at: string | null;
}

export interface SyncStatus {
  configured_item_id: string | null;
  items: {
    id: string;
    connector_name: string | null;
    status: string | null;
    last_synced_at: string | null;
  }[];
}

/** Premissas editáveis na tela (taxas anuais em fração). Só são enviadas quando o usuário muda. */
export interface Assumptions {
  cdi_annual?: number;
  ipca_annual?: number;
  equity_return_annual?: number;
}
