"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { apiPost } from "@/lib/api";

/** Remove this run from the viewer. Reversible: it only hides the run (nothing is deleted from disk), and the
 *  Removed runs page restores it. Two clicks, with the consequence stated before the second. */
export function RemoveRunButton({ hash, backHref }: { hash: string; backHref: string }) {
  const router = useRouter();
  const [stage, setStage] = useState<"idle" | "confirm" | "busy">("idle");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const remove = async () => {
    setStage("busy");
    setError(null);
    try {
      await apiPost(`/api/runs/${hash}/remove`, { reason });
      router.push(backHref);
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStage("confirm");
    }
  };

  if (stage === "idle") {
    return (
      <button type="button" className="btn" onClick={() => setStage("confirm")}>
        Remove run
      </button>
    );
  }
  return (
    <div className="card w-full max-w-md space-y-2 p-3 text-sm sm:w-auto" role="alertdialog" aria-label="Confirm removing this run">
      <p>
        Remove this run from the viewer? Nothing is deleted from disk. You can restore it any time from{" "}
        <b>Removed runs</b>.
      </p>
      <label className="flex flex-col gap-1 text-xs text-ink2">
        Reason (optional)
        <input className="btn" value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} placeholder="e.g. superseded, wrong window" />
      </label>
      {error && <p className="warn">⚠ {error}</p>}
      <div className="flex gap-2">
        <button type="button" className="btn" disabled={stage === "busy"} onClick={remove}>
          {stage === "busy" ? "Removing…" : "Remove"}
        </button>
        <button type="button" className="btn" disabled={stage === "busy"} onClick={() => setStage("idle")}>
          Cancel
        </button>
      </div>
    </div>
  );
}
