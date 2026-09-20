// A paste-ready notebook snippet for a parameter combination the store has no run for.
// The viewer is read-only by design: it never launches a backtest, it hands you the call.
import type { GridResponse, Level, StudyParams } from "./api";

const STRATEGY_CONFIG: Record<string, string> = {
  short_puts: "configs/short_put_45dte.yaml",
  iron_condor: "configs/iron_condor_45dte.yaml",
};

// grid_sweep names each run from its sweep dict -- keys in dict order, values by Python repr -- and the
// name is part of the run's hash. The stored runs were made as (delta, take_profit, stop_loss) with
// float values, e.g. "...|leg1_delta.target=0.16,take_profit=0.25,stop_loss=-2.0". Emit the same order
// and float form so a new run gets the same name as its neighbours (checked against the store).
const KEY_ORDER = ["leg1_delta.target", "take_profit", "stop_loss", "exit_dte", "max_entry_dte"];
const isFloatParam = (k: string) => /(_delta\.|take_profit|stop_loss)/.test(k);

/** "0.3" -> 0.3, "-2" -> -2.0 for float params, "none" -> None, anything else a quoted string. */
function py(key: string, label: string): string {
  if (label === "none") return "None";
  const n = Number(label);
  if (!Number.isFinite(n) || label.trim() === "") return JSON.stringify(label);
  return isFloatParam(key) && Number.isInteger(n) ? n.toFixed(1) : String(n);
}

export function missingRunSnippet(
  grid: GridResponse,
  params: StudyParams,
  x: string,
  y: string | null,
): string {
  const [start, end] = grid.window.includes("..") && grid.window !== "walk-forward" ? grid.window.split("..") : ["", ""];
  const axes: Record<string, string> = { [grid.x.key]: x };
  if (grid.y && y != null) axes[grid.y.key] = y;
  const all = { ...grid.fixed, ...axes };

  // Constants from the study that are plain params (not the dotted grid keys) go on the base config.
  const constants = Object.entries(params.constant)
    .filter(([k]) => k !== "entry_filter" && !k.includes("."))
    .map(([k, v]: [string, Level]) => `${k}=${v == null ? "None" : JSON.stringify(v)}`);
  const filter = params.constant["entry_filter"];
  const yaml = STRATEGY_CONFIG[grid.strategy] ?? `configs/<${grid.strategy}>.yaml`;

  // StrategyConfig defaults to data_source="unified". If the study's runs used another source, pin it,
  // or the new run would not be comparable to the cells around it.
  const sources = [...new Set((grid.cells ?? []).flatMap((c) => c.data_sources ?? []))];
  const source = sources.length === 1 ? sources[0] : null;

  const overrides = [
    start && `start="${start}"`,
    end && `end="${end}"`,
    source && `data_source="${source}"`,
    filter ? `entry_filter=${JSON.stringify(filter)}` : "",
    ...constants,
  ].filter(Boolean);

  const rank = (k: string) => (KEY_ORDER.includes(k) ? KEY_ORDER.indexOf(k) : KEY_ORDER.length);
  const sweep = Object.entries(all)
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([k, v]) => `    "${k}": [${py(k, v)}],`)
    .join("\n");

  return [
    "from lab.backtest import StrategyConfig",
    "from lab.experiments import grid_sweep",
    "",
    `base = StrategyConfig.from_yaml("${yaml}")${overrides.length ? `.replace(${overrides.join(", ")})` : ""}`,
    "grid_sweep(base, {",
    sweep,
    `}, tag="${grid.tag}")`,
    "",
    source
      ? `# data_source="${source}" matches the neighbouring runs (derived from their config hashes). sim is the`
      : `# WARNING: this study mixes chain data sources (${sources.join(", ") || "unknown"}); pick one deliberately. sim is the`,
    "# config default, which every stored run was proven to use. The new run's id (config hash) will not equal",
    "# an older neighbour's even with identical params: runs saved before data_source existed hash without it.",
  ].join("\n");
}
