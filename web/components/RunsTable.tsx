import Link from "next/link";
import type { RunRow } from "@/lib/api";
import { fmt, levelLabel, metricLabel, paramLabel } from "@/lib/format";

const COLS = ["total_trades", "win_rate", "sharpe_ratio", "probabilistic_sharpe", "max_drawdown", "cagr"];

/** Flat run list, for studies that are not a param grid (filter comparisons, walk-forward, single). */
export function RunsTable({ runs, paramKeys, showWindow }: { runs: RunRow[]; paramKeys: string[]; showWindow?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="data">
        <thead>
          <tr>
            {showWindow && <th>Window</th>}
            <th>Entry filter</th>
            {paramKeys.map((k) => <th key={k}>{paramLabel(k)}</th>)}
            {COLS.map((c) => <th key={c}>{metricLabel(c)}</th>)}
            <th>Run</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.hash}>
              {showWindow && <td>{r.window}</td>}
              <td className="max-w-[26ch] truncate" title={r.entry_filter ?? ""}>{r.entry_filter ?? "none"}</td>
              {paramKeys.map((k) => <td key={k}>{levelLabel(r.params[k])}</td>)}
              {COLS.map((c) => <td key={c}>{fmt(c, r.metrics[c])}</td>)}
              <td>
                <Link className="underline decoration-hair underline-offset-2" href={`/run/${r.hash}`}>{r.hash}</Link>
                {r.flags.anecdotal && <span className="warn ml-2">anecdotal</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
