"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPost } from "@/lib/api";

/** Restore, or delete permanently, one removed run. Restore is one click. Permanent delete is behind a second
 *  step where the run's hash must be typed, and says exactly what it will and will not do first. */
export function RemovedActions({ hash, hasTradeLog }: { hash: string; hasTradeLog: boolean }) {
  const router = useRouter();
  const [stage, setStage] = useState<"idle" | "delete" | "busy">("idle");
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<string | null>(null);

  const act = async (path: string, body?: unknown) => {
    setStage("busy");
    setError(null);
    try {
      await apiPost(path, body);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStage(path.endsWith("purge") ? "delete" : "idle");
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn" disabled={stage === "busy"} onClick={() => act(`/api/removed/${hash}/restore`)}>
          Restore
        </button>
        {stage === "idle" && (
          <button type="button" className="btn" onClick={() => setStage("delete")}>
            Delete permanently…
          </button>
        )}
      </div>
      {(stage === "delete" || (stage === "busy" && typed)) && (
        <div className="card max-w-md space-y-2 p-3 text-xs" role="alertdialog" aria-label="Confirm permanent delete">
          <p className="text-sm">
            <span className="warn mr-1">⚠</span>Delete this run permanently?
          </p>
          <ul className="list-disc space-y-0.5 pl-4 text-ink2">
            <li>Its row is removed from <code>runs.parquet</code> (a timestamped backup is written first).</li>
            <li>{hasTradeLog ? "Its trade log is moved to " : "It has no trade log to move; otherwise it would go to "}<code>trades/_deleted/</code>, not erased.</li>
            <li>The viewer cannot bring it back afterwards. Restoring means copying the row from the backup.</li>
            <li>Runs published to the Sophie database stay there.</li>
          </ul>
          <label className="flex flex-col gap-1 text-ink2">
            Type <code className="text-ink">{hash}</code> to confirm
            <input className="btn font-mono" value={typed} onChange={(e) => setTyped(e.target.value.trim())} autoComplete="off" spellCheck={false} />
          </label>
          {error && <p className="warn">⚠ {error}</p>}
          <div className="flex gap-2">
            <button type="button" className="btn" disabled={typed !== hash || stage === "busy"} onClick={() => act(`/api/removed/${hash}/purge`, { confirm: typed })}>
              {stage === "busy" ? "Deleting…" : "Delete permanently"}
            </button>
            <button type="button" className="btn" disabled={stage === "busy"} onClick={() => { setStage("idle"); setTyped(""); setError(null); }}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {stage === "idle" && error && <p className="warn">⚠ {error}</p>}
    </div>
  );
}
