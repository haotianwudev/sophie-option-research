import Link from "next/link";
import { ApiProblem, attempt } from "@/components/ApiProblem";
import { EquityChart } from "@/components/EquityChart";
import { MetricTiles } from "@/components/MetricTiles";
import { RegimeTable } from "@/components/RegimeTable";
import { TradeTable } from "@/components/TradeTable";
import { apiGet, studyHref, type RunDetail } from "@/lib/api";
import { fmt, levelLabel, paramLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card space-y-3 p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}

const HEADLINE_PARAMS = ["leg1_delta.target", "take_profit", "stop_loss", "exit_dte", "max_entry_dte"];

/** A readable title from the params, instead of the raw config-name string. */
function headline(params: Record<string, unknown>, entryFilter: string | null): string {
  const parts = HEADLINE_PARAMS.filter((k) => k in params).map((k) => `${paramLabel(k)} ${levelLabel(params[k])}`);
  if (entryFilter) parts.push(`when ${entryFilter}`);
  return parts.join(" · ");
}

export default async function RunPage({ params }: { params: Promise<{ hash: string }> }) {
  const { hash } = await params;
  const [run, err] = await attempt(apiGet<RunDetail>(`/api/runs/${encodeURIComponent(hash)}`));
  if (err || !run) return <ApiProblem error={err ?? "no data"} />;
  const study = { strategy: run.strategy, tag: run.tags[0] ?? run.tag, window: run.study_window };

  return (
    <div className="space-y-5">
      <div>
        <p className="text-sm text-ink2">
          <Link href="/" className="underline decoration-hair underline-offset-2">Strategies</Link> /{" "}
          <Link href={`/s/${encodeURIComponent(run.strategy)}`} className="underline decoration-hair underline-offset-2">{run.strategy.replace(/_/g, " ")}</Link> /{" "}
          <Link href={studyHref(study)} className="underline decoration-hair underline-offset-2">{study.tag} · {study.window}</Link> /
        </p>
        <h1 className="text-2xl font-semibold">{headline(run.params, run.entry_filter) || run.name}</h1>
        <p className="tnum mt-1 text-sm text-ink2">
          {run.name.split("|")[0]} · {run.hash} · window {run.window} · tag {run.tags.join(", ")} · run {run.run_at.replace("T", " ")}
        </p>
      </div>

      {run.same_params_other_hashes.length > 0 && (
        <div className="card space-y-2 border-l-4 p-4" style={{ borderLeftColor: "var(--warn)" }}>
          <p><span className="warn">≠ Same parameters, other runs</span></p>
          <p className="text-sm text-ink2">
            The store holds this configuration under {run.same_params_other_hashes.length + 1} hashes
            {run.same_params_other_hashes.some((o) => o.total_trades !== run.metrics.total_trades || o.sharpe_ratio !== run.metrics.sharpe_ratio)
              ? ", and their results differ."
              : ", with identical results."}{" "}
            {new Set([run.data_source, ...run.same_params_other_hashes.map((o) => o.data_source)]).size > 1
              ? "They used different chain data — see the source column. The store does not save this; it is derived by re-hashing each run's config, which matches exactly."
              : "They used the same chain data source."}
          </p>
          <table className="data">
            <thead><tr><th>Run</th><th>Run at</th><th>Chain data</th><th>Trades</th><th>Sharpe</th></tr></thead>
            <tbody>
              {[{ hash: run.hash, run_at: run.run_at, data_source: run.data_source, total_trades: run.metrics.total_trades, sharpe_ratio: run.metrics.sharpe_ratio }, ...run.same_params_other_hashes].map((o) => (
                <tr key={o.hash}>
                  <td>{o.hash === run.hash ? <b>{o.hash} (this run)</b> : <Link className="underline decoration-hair underline-offset-2" href={`/run/${o.hash}`}>{o.hash}</Link>}</td>
                  <td>{o.run_at.replace("T", " ")}</td>
                  <td>{o.data_source ?? "unknown"}</td>
                  <td>{fmt("total_trades", o.total_trades)}</td>
                  <td>{fmt("sharpe_ratio", o.sharpe_ratio)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <MetricTiles run={run} />

      {run.has_trade_log ? (
        <>
          <Section title="Equity and drawdown"><EquityChart hash={run.hash} /></Section>
          <Section title="Performance by entry regime"><RegimeTable hash={run.hash} /></Section>
          <Section title="Trades"><TradeTable hash={run.hash} /></Section>
        </>
      ) : (
        <p className="warn inline-block">⚠ This run has no trade log on disk.</p>
      )}

      <Section title="Parameters">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-ink2">Entry filter</dt>
          <dd>{run.entry_filter ?? "none"}</dd>
          <dt className="text-ink2">Chain data</dt>
          <dd title={run.provenance}>{run.data_source ?? "unknown"}{run.data_source === "legacy" ? " (OptionsDX 2010–2023)" : run.data_source === "unified" ? " (unified archive)" : ""}</dd>
          {run.sim && (
            <>
              <dt className="text-ink2">Simulation</dt>
              <dd className="tnum">capital ${run.sim.capital.toLocaleString()} · quantity {run.sim.quantity} · max positions {run.sim.max_positions}</dd>
            </>
          )}
          {Object.entries(run.params).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-ink2">{paramLabel(k)}</dt>
              <dd className="tnum">{levelLabel(v)}</dd>
            </div>
          ))}
        </dl>
        <details className="text-sm">
          <summary className="cursor-pointer text-ink2">Full params as stored (including delta band edges)</summary>
          <pre className="mt-2 overflow-x-auto rounded-lg border border-hair bg-page p-3 text-xs">{JSON.stringify(run.params_full, null, 2)}</pre>
        </details>
        <p className="text-xs text-ink2">
          The store does not save the chain data source or the simulation settings. Both are derived here by
          re-hashing this run&apos;s config and matching its id ({run.provenance}). Backtests use EOD mid fills with no
          slippage or commission unless a run says otherwise.
        </p>
      </Section>
    </div>
  );
}
