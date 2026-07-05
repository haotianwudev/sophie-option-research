"""Reporting: tearsheets, run comparison, regime breakdown, parameter heatmaps."""

import re
from pathlib import Path
from typing import Optional

import pandas as pd

from . import RESULTS_DIR
from .backtest import RunResult, load_features
from .experiments import load_runs, load_trades

REPORT_DIR = RESULTS_DIR / "reports"


def daily_returns(equity_curve: pd.Series) -> pd.Series:
    """Per-trade equity curve (indexed by exit date) -> daily return series."""
    eq = equity_curve.copy()
    eq.index = pd.to_datetime(eq.index)
    eq = eq.groupby(eq.index).last()
    daily = eq.resample("D").ffill().dropna()
    return daily.pct_change().fillna(0.0)


def tearsheet(result: RunResult, benchmark: Optional[pd.Series] = None) -> Path:
    """Write a quantstats HTML tearsheet; returns the report path."""
    import quantstats as qs

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r'[<>:"/\\|?*,= ]+', "_", result.config.name)
    out = REPORT_DIR / f"{safe_name}_{result.config_hash}.html"
    qs.reports.html(
        daily_returns(result.equity_curve),
        benchmark=benchmark,
        output=str(out),
        title=result.config.name,
    )
    return out


def compare_runs(hashes: Optional[list[str]] = None, tag: Optional[str] = None) -> pd.DataFrame:
    """Side-by-side metrics from the results store, best Sharpe first."""
    runs = load_runs(tag=tag)
    if runs.empty:
        return runs
    if hashes:
        runs = runs[runs["config_hash"].isin(hashes)]
    cols = ["name", "config_hash", "entry_filter", "total_trades", "win_rate",
            "total_return", "sharpe_ratio", "sortino_ratio", "max_drawdown",
            "profit_factor", "calmar_ratio"]
    return (
        runs[[c for c in cols if c in runs.columns]]
        .sort_values("sharpe_ratio", ascending=False)
        .reset_index(drop=True)
    )


def regime_breakdown(
    trade_log: pd.DataFrame,
    feature: str = "vix_rank",
    bins: int = 4,
    features: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Trade performance grouped by quantile bucket of an entry-day feature.

    Works with either a simulate trade_log (entry_date/realized_pnl) or an
    ML dataset (entry_date/pct_change).
    """
    if features is None:
        features = load_features()
    pnl_col = "realized_pnl" if "realized_pnl" in trade_log.columns else "pct_change"
    t = trade_log.merge(
        features[["quote_date", feature]],
        left_on="entry_date", right_on="quote_date", how="left",
    )
    t["bucket"] = pd.qcut(t[feature], bins, duplicates="drop")
    grouped = t.groupby("bucket", observed=True)[pnl_col]
    return pd.DataFrame({
        "trades": grouped.size(),
        "win_rate": grouped.apply(lambda s: (s > 0).mean()),
        f"avg_{pnl_col}": grouped.mean(),
        f"total_{pnl_col}": grouped.sum(),
    })


def param_heatmap(
    sweep: pd.DataFrame,
    x: str,
    y: str,
    metric: str = "sharpe_ratio",
    ax=None,
):
    """Pivot a grid_sweep result into a metric heatmap (matplotlib)."""
    import matplotlib.pyplot as plt

    pivot = sweep.pivot_table(index=y, columns=x, values=metric, aggfunc="mean")
    if ax is None:
        _, ax = plt.subplots(figsize=(1.2 * len(pivot.columns) + 2, 0.6 * len(pivot) + 2))
    im = ax.imshow(pivot.to_numpy(), aspect="auto", cmap="RdYlGn")
    ax.set_xticks(range(len(pivot.columns)), [str(c) for c in pivot.columns])
    ax.set_yticks(range(len(pivot.index)), [str(i) for i in pivot.index])
    ax.set_xlabel(x)
    ax.set_ylabel(y)
    ax.set_title(metric)
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            val = pivot.iat[i, j]
            if pd.notna(val):
                ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=9)
    plt.colorbar(im, ax=ax, shrink=0.8)
    return ax
