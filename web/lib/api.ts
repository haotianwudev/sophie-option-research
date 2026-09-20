// Typed client for the local research API (FastAPI on 127.0.0.1:8010). Read-only.
// The same origin is used server-side (RSC) and from the browser; the API allowlists this UI's
// origin for CORS.
export const API = process.env.NEXT_PUBLIC_API ?? "http://127.0.0.1:8010";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(`${status}: ${detail}`);
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export type Level = number | string | null;
export type Kind = "grid" | "scatter" | "line" | "filter_set" | "walk_forward" | "single";

export interface Health {
  store: string;
  n_runs: number;
  n_metrics: number;
  mtime: string;
  n_trade_logs: number;
  not_recorded_in_store: string[];
  data_sources: Record<string, number>;
}
export interface StrategySummary {
  id: string;
  n_runs: number;
  n_studies: number;
  tags: string[];
  date_span: [string | null, string | null];
}
export interface VaryingBrief {
  key: string;
  kind: "discrete" | "continuous" | "categorical";
  n_levels: number;
}
export interface StudySummary {
  strategy: string;
  tag: string;
  window: string;
  n_runs: number;
  kind: Kind;
  varying: VaryingBrief[];
  best: { hash: string; metric: string; value: number } | null;
  median?: { metric: string; value: number } | null;
  n_duplicate_runs: number;
  data_sources: (string | null)[];
  runs_from: string;
  runs_to: string;
}
export interface ParamInfo extends VaryingBrief {
  levels: Level[];
  labels: string[];
  counts: Record<string, number>;
}
export interface StudyParams {
  strategy: string;
  tag: string;
  window: string;
  n_runs: number;
  kind: Kind;
  varying: ParamInfo[];
  constant: Record<string, Level>;
  default_axes: string[];
  metrics: string[];
  default_metric: string;
}
export interface AxisInfo {
  key: string;
  kind: string;
  levels: Level[];
  labels: string[];
}
export interface Cell {
  x: string;
  y: string | null;
  ran: boolean;
  n_runs: number;
  hash: string | null;
  value: number | null;
  conflict: boolean;
  spread: number | null;
  hashes: string[];
  data_sources?: string[];
  total_trades: number | null;
}
export interface Point {
  hash: string;
  x: number | null;
  y: number | null;
  value: number | null;
  total_trades: number | null;
  entry_filter: string | null;
  run_at: string;
}
export interface GridResponse {
  strategy: string;
  tag: string;
  window: string;
  kind: "grid" | "scatter";
  study_kind: Kind;
  metric: string;
  x: AxisInfo;
  y: AxisInfo | null;
  fixed: Record<string, string>;
  free: { key: string; kind: string; labels: string[]; counts: Record<string, number> }[];
  n_runs_shown: number;
  metric_range: [number | null, number | null];
  cells?: Cell[];
  n_cells?: number;
  n_cells_run?: number;
  n_cells_conflict?: number;
  points?: Point[];
}
export interface RunRow {
  hash: string;
  name: string;
  strategy: string;
  tag: string;
  window: string;
  study_window: string;
  entry_filter: string | null;
  run_at: string;
  params: Record<string, Level>;
  data_source: string | null;
  provenance: string;
  sim: { capital: number; quantity: number; max_positions: number } | null;
  metrics: Record<string, number | null>;
  flags: { anecdotal: boolean; edge_established: boolean | null };
}
export interface RunDetail extends RunRow {
  tags: string[];
  params_full: Record<string, unknown>;
  has_trade_log: boolean;
  not_recorded_in_store: string[];
  same_params_other_hashes: { hash: string; run_at: string; data_source: string | null; total_trades: number | null; sharpe_ratio: number | null }[];
}
export interface TradesResp {
  total: number;
  offset: number;
  limit: number;
  rows: Record<string, string | number | null>[];
  by_exit_type: { exit_type: string; n: number; total: number; mean: number }[];
}
export interface EquityResp {
  n_trades: number;
  capital_inferred: number | null;
  points: { date: string; equity: number; drawdown: number }[];
  note: string;
}
export interface RegimesResp {
  feature: string;
  bins: number;
  rows: { bucket: string; trades: number; win_rate: number; avg_pnl: number; total_pnl: number }[];
  features_available?: string[];
  note?: string;
}
export interface Memo {
  study_key: string;
  generated_at: string;
  hypothesis: string;
  method: { strategy: string[]; windows: string[]; n_runs: number; shared_params: Record<string, unknown> };
  results: Record<string, string | number | null>[];
  best_run: Record<string, string | number | null> | null;
  caveats: string[];
  notes: string;
  headline_metrics: string[];
}

/** Study addresses go in the URL path; windows like `2016-01-01..2023-12-31` need encoding. */
export const studyHref = (s: { strategy: string; tag: string; window: string }) =>
  `/s/${encodeURIComponent(s.strategy)}/${encodeURIComponent(s.tag)}/${encodeURIComponent(s.window)}`;
