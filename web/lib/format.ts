// Metric presentation. Fractions the store keeps as 0-1 are shown as percentages; the rest as
// plain numbers. Labels are sentence case, no trailing colon.
const PCT = new Set([
  "win_rate", "max_drawdown", "cagr", "total_return", "exposure", "premium_capture",
  "ann_vol", "probabilistic_sharpe", "avg_return_on_margin", "ann_return_on_margin",
]);
const USD = new Set(["total_pnl", "avg_pnl", "avg_win", "avg_loss", "max_win", "max_loss", "pnl_per_day_in_trade"]);
const INT = new Set(["total_trades", "winning_trades", "losing_trades", "max_dd_days"]);

export const METRIC_LABEL: Record<string, string> = {
  total_trades: "Trades",
  win_rate: "Win rate",
  premium_capture: "Premium capture",
  sharpe_ratio: "Sharpe",
  probabilistic_sharpe: "Probabilistic Sharpe",
  sortino_ratio: "Sortino",
  max_drawdown: "Max drawdown",
  pnl_per_day_in_trade: "P&L per day in trade",
  worst_trade_over_avg_credit: "Worst trade / avg credit",
  cagr: "CAGR",
  total_return: "Total return",
  total_pnl: "Total P&L",
  profit_factor: "Profit factor",
  calmar_ratio: "Calmar",
  omega_ratio: "Omega",
  ann_vol: "Annualized vol",
  exposure: "Exposure",
  max_dd_days: "Max drawdown days",
  trade_pnl_tstat: "Trade P&L t-stat",
};
export const metricLabel = (m: string) =>
  METRIC_LABEL[m] ?? m.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function fmt(metric: string, v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (INT.has(metric)) return Math.round(v).toLocaleString();
  if (PCT.has(metric)) return `${(v * 100).toFixed(Math.abs(v) < 0.1 ? 1 : 0).replace("-", "−")}%`;
  if (USD.has(metric)) return `${v < 0 ? "−" : ""}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
  return v.toFixed(Math.abs(v) < 10 ? 2 : 1).replace("-", "−");
}

/** Param level for display: a missing take_profit / stop_loss is a real choice, "none". */
export const levelLabel = (l: unknown) =>
  l == null ? "none" : typeof l === "number" ? String(+l.toFixed(4)) : String(l);

export const PARAM_LABEL: Record<string, string> = {
  "leg1_delta.target": "Delta (target)",
  take_profit: "Take profit",
  stop_loss: "Stop loss",
  exit_dte: "Exit DTE",
  max_entry_dte: "Entry DTE",
  entry_filter: "Entry filter",
};
export const paramLabel = (k: string) => PARAM_LABEL[k] ?? k;
