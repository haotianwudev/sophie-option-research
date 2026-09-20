/** Auto-generated memo caveats. They are the point of a study page, not a footnote, so they get a
 *  visible icon + label rather than colour alone. */
export function CaveatList({ caveats }: { caveats: string[] }) {
  if (!caveats.length) return null;
  return (
    <section aria-labelledby="caveats" className="card p-4">
      <h2 id="caveats" className="mb-2 flex items-center gap-2 text-sm font-semibold">
        <span className="warn">⚠ Caveats</span>
        <span className="font-normal text-ink2">read these before the numbers</span>
      </h2>
      <ul className="space-y-1.5 text-sm text-ink2">
        {caveats.map((c) => (
          <li key={c} className="flex gap-2">
            <span aria-hidden className="text-muted">•</span>
            <span>{c}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
