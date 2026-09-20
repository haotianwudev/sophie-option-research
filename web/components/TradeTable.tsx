"use client";

import { useEffect, useState } from "react";
import { apiGet, type TradesResp } from "@/lib/api";
import { fmt } from "@/lib/format";

const PAGE = 25;
const usd = (v: unknown) => (typeof v === "number" ? fmt("total_pnl", v) : "—");

/** Trade log, paged from the API, with the exit-type breakdown up top (take_profit / stop_loss / ...). */
export function TradeTable({ hash }: { hash: string }) {
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<TradesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    apiGet<TradesResp>(`/api/runs/${hash}/trades?offset=${offset}&limit=${PAGE}`)
      .then((d) => setData((prev) => (prev && offset > 0 ? { ...d, by_exit_type: prev.by_exit_type } : d)))
      .catch((e) => setErr(String(e.message ?? e)));
  }, [hash, offset]);

  if (err) return <p className="warn inline-block">⚠ {err}</p>;
  if (!data) return <div className="h-40 animate-pulse rounded-lg bg-grid" aria-busy />;
  const last = Math.min(offset + PAGE, data.total);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2 text-xs">
        {data.by_exit_type.map((e) => (
          <span key={e.exit_type} className="rounded-full border border-hair px-2.5 py-1 text-ink2">
            {e.exit_type}: <b className="tnum text-ink">{e.n}</b> trades · avg <span className="tnum">{usd(e.mean)}</span>
          </span>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="data">
          <thead>
            <tr>
              <th>#</th><th>Entry</th><th>Exit</th><th>Expiry</th><th>Strike</th><th>Exit type</th>
              <th>Days</th><th>Credit (pts)</th><th>P&L</th><th>Equity</th>
            </tr>
          </thead>
          <tbody>
            {data.rows.map((r) => (
              <tr key={String(r.trade_id)}>
                <td>{r.trade_id}</td><td>{r.entry_date}</td><td>{r.exit_date}</td><td>{r.expiration}</td>
                <td>{r.strike ?? "—"}</td><td>{r.exit_type}</td><td>{r.days_held}</td>
                <td>{typeof r.entry_cost === "number" ? (-r.entry_cost).toFixed(2) : "—"}</td>
                <td>{usd(r.realized_pnl)}</td><td>{usd(r.equity)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex items-center gap-3 text-xs text-ink2">
        <button type="button" className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</button>
        <span className="tnum">{data.total ? `${offset + 1}–${last} of ${data.total}` : "no trades"}</span>
        <button type="button" className="btn" disabled={last >= data.total} onClick={() => setOffset(offset + PAGE)}>Next</button>
      </div>
    </div>
  );
}
