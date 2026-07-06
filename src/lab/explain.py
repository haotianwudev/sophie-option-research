"""Explanation layer: structured research memos per study.

A memo is the machine-readable summary of one study (a results-store tag):
what was tested, how, the headline numbers, and the caveats a reader needs
before believing anything. The memo feeds three consumers: a rendered
markdown report, the option_research_evaluation DB table, and the
/option-research-explain skill that writes the AI narrative from it.
"""

import json
from datetime import datetime
from typing import Optional

import pandas as pd

from . import RESULTS_DIR
from .experiments import load_runs

HEADLINE_METRICS = [
    "total_trades", "win_rate", "premium_capture", "sharpe_ratio",
    "probabilistic_sharpe", "sortino_ratio", "max_drawdown",
    "pnl_per_day_in_trade", "worst_trade_over_avg_credit", "cagr",
]


def build_memo(
    study_tag: str,
    hypothesis: str = "",
    notes: str = "",
) -> dict:
    """Assemble the structured memo for every stored run under *study_tag*."""
    runs = load_runs(tag=study_tag)
    if runs.empty:
        raise ValueError(f"no runs with tag '{study_tag}' in the results store")

    rows = []
    for _, r in runs.iterrows():
        row = {"name": r["name"], "entry_filter": r["entry_filter"] or "(none)",
               "config_hash": r["config_hash"]}
        row.update({m: round(float(r[m]), 4) for m in HEADLINE_METRICS
                    if m in runs.columns and pd.notna(r[m])})
        rows.append(row)
    results = sorted(rows, key=lambda x: x.get("sharpe_ratio", float("-inf")),
                     reverse=True)

    # walk-forward runs under this tag mean some numbers are honest OOS
    has_oos = bool((load_runs(tag="wf_oos").shape[0]) if study_tag != "wf_oos" else True)

    caveats = [
        "Fills are EOD mid prices; no intraday triggers.",
        "SPX options are European-style / cash-settled; no early assignment modeled.",
    ]
    params = json.loads(runs.iloc[0]["params_json"]) if "params_json" in runs.columns else {}
    if "slippage" not in params and "commission" not in params:
        caveats.append("No slippage or commission modeled — selective-entry "
                       "variants trade less and are penalized least by real costs.")
    if not has_oos:
        caveats.append("All results are IN-SAMPLE. Run the walk-forward harness "
                       "before acting on any parameter choice.")
    else:
        caveats.append("Unless a run is tagged wf_oos, its numbers are in-sample.")
    low_n = [r["name"] for r in rows if r.get("total_trades", 99) < 30]
    if low_n:
        caveats.append(f"Low trade counts (<30) — treat as anecdotal: {', '.join(low_n)}.")

    windows = runs[["start", "end"]].drop_duplicates()
    return {
        "study_key": study_tag,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "hypothesis": hypothesis,
        "method": {
            "strategy": sorted(runs["strategy"].unique().tolist()),
            "windows": [f"{a or 'data-start'}..{b or 'data-end'}"
                        for a, b in windows.itertuples(index=False)],
            "n_runs": len(runs),
            "shared_params": params,
        },
        "results": results,
        "best_run": results[0] if results else None,
        "caveats": caveats,
        "notes": notes,
    }


def render_memo(memo: dict, save: bool = True) -> str:
    """Memo dict -> markdown; optionally saved to results/reports/."""
    lines = [
        f"# Research memo — {memo['study_key']}",
        f"*Generated {memo['generated_at']}*",
        "",
    ]
    if memo["hypothesis"]:
        lines += ["## Hypothesis", memo["hypothesis"], ""]
    m = memo["method"]
    lines += [
        "## Method",
        f"- Strategy: {', '.join(m['strategy'])}",
        f"- Window(s): {', '.join(m['windows'])}",
        f"- Runs: {m['n_runs']}",
        f"- Shared params: `{json.dumps(m['shared_params'])}`",
        "",
        "## Results (sorted by Sharpe)",
    ]
    df = pd.DataFrame(memo["results"])
    lines.append(df.to_markdown(index=False))
    lines += ["", "## Caveats"]
    lines += [f"- {c}" for c in memo["caveats"]]
    if memo.get("notes"):
        lines += ["", "## Notes", memo["notes"]]
    text = "\n".join(lines)
    if save:
        out = RESULTS_DIR / "reports" / f"memo_{memo['study_key']}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def memo_path(study_tag: str):
    return RESULTS_DIR / "reports" / f"memo_{study_tag}.md"


def publish_study(study_tag: str, hypothesis: str = "", narrative: str = "",
                  model: str = "", featured_hashes: Optional[list] = None) -> dict:
    """One-call publication: memo + runs + evaluation to the Sophie DB."""
    from .db import ensure_tables, push_evaluation, push_study

    memo = build_memo(study_tag, hypothesis=hypothesis)
    render_memo(memo)
    ensure_tables()
    n = push_study(study_tag, featured_hashes=featured_hashes)
    push_evaluation(study_tag, memo, narrative=narrative, model=model)
    memo["pushed_runs"] = n
    return memo
