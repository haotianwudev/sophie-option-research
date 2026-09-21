import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { RemovedActions } from "@/components/RemovedActions";
import { apiGet, type RemovedRun } from "@/lib/api";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function RemovedPage() {
  const [runs, err] = await attempt(apiGet<RemovedRun[]>("/api/removed"));
  if (err || !runs) return <ApiProblem error={err ?? "no data"} />;

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-ink2"><Link href="/" className="underline decoration-hair underline-offset-2">Strategies</Link> /</p>
        <h1 className="text-2xl font-semibold">Removed runs</h1>
        <p className="mt-1 text-sm text-ink2">
          Runs hidden from every view. Removing only hides a run — its data is untouched — so Restore puts it back
          exactly as it was. Delete permanently is a separate step that needs the run&apos;s hash typed as confirmation.
        </p>
      </div>

      {runs.length === 0 ? (
        <div className="card p-6 text-sm text-ink2">No removed runs. Use “Remove run” on a run&apos;s page to hide one.</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="data">
            <thead>
              <tr><th>Run</th><th>Study</th><th>Removed</th><th>Reason</th><th>Trades</th><th>Sharpe</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.hash} className="align-top">
                  <td className="max-w-[34ch] break-words">
                    <div className="font-mono text-xs">{r.hash}</div>
                    <div className="text-ink2">{r.name.split("|").slice(1).join("|") || r.name}</div>
                  </td>
                  <td className="whitespace-nowrap">{r.strategy.replace(/_/g, " ")} · {r.tag}<div className="text-xs text-ink2">{r.window}</div></td>
                  <td className="whitespace-nowrap">{r.removed_at.replace("T", " ")}</td>
                  <td className="max-w-[24ch] break-words text-ink2">{r.reason || "—"}</td>
                  <td>{r.in_store ? fmt("total_trades", r.total_trades) : "—"}</td>
                  <td>{r.in_store ? fmt("sharpe_ratio", r.sharpe_ratio) : "—"}</td>
                  <td><RemovedActions hash={r.hash} hasTradeLog={r.has_trade_log} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
