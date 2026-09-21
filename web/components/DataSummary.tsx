import Link from "next/link";
import type { Availability } from "@/lib/availability-types";

const LABEL = { ok: "Up to date", warning: "Falling behind", stale: "Stale" } as const;

/** One-line data status for the home page. The full picture is on /data; this only answers "is my data
 *  current?". `a` is null when the availability call timed out (the first call after an API restart scans the
 *  whole archive), in which case it says so instead of holding up the page. */
export function DataSummary({ a }: { a: Availability | null }) {
  const f = a?.freshness;
  const cov = a?.coverage;
  return (
    <Link href="/data" className="card flex flex-wrap items-center gap-x-4 gap-y-1 p-3 text-sm hover:bg-grid">
      <span className="font-semibold">Chain data</span>
      {a && f && cov ? (
        <>
          <span className={f.status === "ok" ? "text-pos" : "warn"}>
            {f.status === "ok" ? "✓" : "⚠"} {LABEL[f.status]}
          </span>
          <span className="tnum text-ink2">
            newest {f.latest_chain}
            {f.lag_sessions > 0 && ` · ${f.lag_sessions} session${f.lag_sessions === 1 ? "" : "s"} behind`}
          </span>
          <span className="tnum text-ink2">
            {cov.missing_sessions.length} gap{cov.missing_sessions.length === 1 ? "" : "s"} · {cov.files_on_non_sessions.length} closed-day file
            {cov.files_on_non_sessions.length === 1 ? "" : "s"}
          </span>
        </>
      ) : (
        <span className="text-ink2">still checking the archive, open the details</span>
      )}
      <span className="ml-auto text-ink2 underline decoration-hair underline-offset-2">Details</span>
    </Link>
  );
}
