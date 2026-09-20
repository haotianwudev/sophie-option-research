import { notFound } from "next/navigation";
import { ApiError } from "@/lib/api";

/** Shown when the local API can't be reached or refuses a request. The viewer is only a window onto
 *  the API, so the useful thing to say is how to start it. */
export function ApiProblem({ error }: { error: string }) {
  const unreachable = /fetch failed|ECONNREFUSED|Failed to fetch/i.test(error);
  return (
    <div className="card space-y-2 p-5">
      <p className="warn inline-block">⚠ {unreachable ? "The research API is not reachable" : "The research API returned an error"}</p>
      <p className="break-words text-sm text-ink2">{error}</p>
      {unreachable && (
        <>
          <p className="text-sm text-ink2">Start it from the sophie-option-research repo root:</p>
          <pre className="overflow-x-auto rounded-lg border border-hair bg-page p-3 text-xs">
            {"PYTHONPATH=src ./.venv/Scripts/python.exe scripts/serve_api.py"}
          </pre>
        </>
      )}
    </div>
  );
}

/** Await a request without throwing, so a page can render the problem in place. */
export async function attempt<T>(p: Promise<T>): Promise<[T, null] | [null, string]> {
  try {
    return [await p, null];
  } catch (e) {
    // A hash/study that is not in the store is a real 404, not an "API problem".
    if (e instanceof ApiError && e.status === 404) notFound();
    return [null, e instanceof Error ? e.message : String(e)];
  }
}
