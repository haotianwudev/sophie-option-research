"use client";

import { useEffect, useState } from "react";
import { apiGet, type RegimesResp } from "@/lib/api";
import { fmt } from "@/lib/format";

/** Performance by quantile of an entry-day feature (VIX rank, RSI, ...). */
export function RegimeTable({ hash }: { hash: string }) {
  const [feature, setFeature] = useState("vix_rank");
  const [data, setData] = useState<RegimesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    apiGet<RegimesResp>(`/api/runs/${hash}/regimes?feature=${encodeURIComponent(feature)}&bins=4`)
      .then(setData)
      .catch((e) => setErr(String(e.message ?? e)));
  }, [hash, feature]);

  return (
    <div className="space-y-3">
      <label className="flex w-fit flex-col gap-1 text-xs text-ink2">
        Feature at entry
        <select className="btn" value={feature} onChange={(e) => setFeature(e.target.value)}>
          {(data?.features_available ?? ["vix_rank", "rsi14", "vol_risk_premium", "vrp_z", "term_slope"]).map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      </label>
      {err && <p className="warn inline-block">⚠ {err}</p>}
      {data && (
        <table className="data">
          <thead><tr><th>Quartile</th><th>Trades</th><th>Win rate</th><th>Avg P&L</th><th>Total P&L</th></tr></thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={r.bucket}>
                <td>{r.bucket}</td><td>{r.trades}</td><td>{fmt("win_rate", r.win_rate)}</td>
                <td>{fmt("total_pnl", r.avg_pnl)}</td><td>{fmt("total_pnl", r.total_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data?.note && <p className="text-xs text-ink2">{data.note}</p>}
    </div>
  );
}
