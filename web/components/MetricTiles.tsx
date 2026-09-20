import type { RunRow } from "@/lib/api";
import { fmt, metricLabel } from "@/lib/format";

// explain.py's HEADLINE_METRICS, in its order.
const HEADLINE = [
  "total_trades", "win_rate", "premium_capture", "sharpe_ratio", "probabilistic_sharpe",
  "sortino_ratio", "max_drawdown", "pnl_per_day_in_trade", "worst_trade_over_avg_credit", "cagr",
];

/** Headline metric tiles, with the research-discipline flags the platform's own memo applies:
 *  probabilistic Sharpe below 0.9 means the edge is not established; under 30 trades is anecdotal. */
export function MetricTiles({ run }: { run: RunRow }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-xs">
        {run.flags.edge_established === false && (
          <span className="warn">⚠ edge not established (probabilistic Sharpe under 0.9)</span>
        )}
        {run.flags.edge_established === null && <span className="warn">probabilistic Sharpe not computed</span>}
        {run.flags.anecdotal && <span className="warn">⚠ anecdotal: fewer than 30 trades</span>}
        {run.flags.edge_established === true && !run.flags.anecdotal && (
          <span className="text-ink2">Probabilistic Sharpe at or above 0.9 with 30+ trades. Still in-sample unless tagged wf_oos.</span>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
        {HEADLINE.filter((m) => m in run.metrics).map((m) => (
          <div key={m} className="card p-3">
            <dt className="text-xs text-ink2">{metricLabel(m)}</dt>
            <dd className="tnum mt-1 text-2xl font-semibold leading-none">{fmt(m, run.metrics[m])}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
