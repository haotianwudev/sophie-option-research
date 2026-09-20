// Response of GET /api/data/availability (see src/lab/api/availability.py).
export interface Availability {
  as_of: string;
  archive: { exists: boolean; path: string; n_files?: number; first?: string; last?: string; unreadable_files?: number };
  freshness?: {
    status: "ok" | "warning" | "stale";
    lag_sessions: number;
    latest_chain: string;
    latest_expected_session: string;
    sessions_since: string[];
    thresholds: { ok_at_most: number; warning_at_most: number };
    note: string;
  };
  coverage?: {
    expected_sessions: number;
    present_sessions: number;
    missing_sessions: string[];
    files_on_non_sessions: string[];
  };
  content?: {
    thin_days: {
      date: string;
      rows: number;
      median_rows: number | null;
      expirations: number;
      median_expirations: number | null;
      source: string;
      error: string | null;
    }[];
    rule: string;
  };
  segments?: {
    source: string;
    roots: string;
    from: string;
    to: string;
    days: number;
    median_rows: number;
    median_expirations: number;
  }[];
  years?: {
    year: number;
    expected: number;
    present: number;
    missing: number;
    median_rows: number | null;
    median_expirations: number | null;
    sources: string[];
    thin_days: number;
  }[];
  months?: { year: number; month: number; expected: number; present: number }[];
  series?: { date: string[]; rows: number[]; expirations: number[] };
  calendar?: {
    validated: boolean;
    against?: string;
    from?: string;
    to?: string;
    sessions_compared?: number;
    calendar_only?: string[];
    prices_only?: string[];
    reason?: string;
  };
  market_caches: Record<string, { exists: boolean; rows?: number; first?: string; last?: string; age_days?: number }>;
  legacy_processed: { exists: boolean; n_files?: number; first?: string; last?: string; missing_months?: string[] };
}
