"""Options-research metrics beyond optopsy's generic risk stats.

Three groups, all returned by compute_run_metrics as one flat dict:

- Options-specific: premium capture, return on margin (RegT-style BPR proxy),
  P&L per day in trade, market exposure, tail-vs-credit ratio.
- Statistical honesty: CAGR, annualized vol, trade-mean t-stat, skew/kurtosis,
  probabilistic Sharpe (Bailey & Lopez de Prado) — is the Sharpe > 0 claim
  even statistically distinguishable from luck?
- Benchmark-relative: correlation, beta, annualized alpha and excess CAGR vs a
  benchmark closing-price series (SPX, CBOE PUT, ...).

Sign conventions follow optopsy's simulate trade log: entry_cost < 0 means a
credit was received; realized_pnl is in dollars.
"""

import math
from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------


def daily_equity(equity_curve: pd.Series) -> pd.Series:
    """Per-trade equity curve (indexed by exit date) -> daily-frequency curve."""
    eq = equity_curve.copy()
    eq.index = pd.to_datetime(eq.index)
    eq = eq.groupby(eq.index).last()
    return eq.resample("D").ffill().dropna()


def probabilistic_sharpe(returns: pd.Series, sr_benchmark: float = 0.0) -> float:
    """PSR (Bailey & Lopez de Prado 2012): P[true SR > sr_benchmark].

    Uses the non-annualized per-period SR and adjusts for skewness/kurtosis of
    the return distribution. ~0.5 means the observed Sharpe is
    indistinguishable from the benchmark; >0.95 is conventional confidence.
    """
    from scipy import stats

    r = pd.Series(returns).dropna()
    n = len(r)
    if n < 10 or r.std(ddof=1) == 0:
        return float("nan")
    sr = r.mean() / r.std(ddof=1)
    skew = stats.skew(r)
    kurt = stats.kurtosis(r, fisher=False)  # raw kurtosis (normal = 3)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr**2
    if denom <= 0:
        return float("nan")
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def _parse_strike(description: str) -> Optional[float]:
    """optopsy single-leg description looks like 'p 4155.0'."""
    try:
        return float(str(description).split()[-1])
    except (ValueError, IndexError):
        return None


def _short_put_margin(underlying: float, strike: float, credit: float) -> float:
    """RegT-style buying-power proxy for a naked short put (per contract).

    max(20% of underlying - OTM amount, 10% of strike) * 100 + credit received.
    """
    otm = max(underlying - strike, 0.0)
    return max(0.20 * underlying - otm, 0.10 * strike) * 100 + credit


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute_run_metrics(
    trade_log: pd.DataFrame,
    equity_curve: pd.Series,
    features: Optional[pd.DataFrame] = None,   # for underlying price at entry
    benchmark_close: Optional[pd.Series] = None,  # datetime-indexed closes
    benchmark_name: str = "bench",
) -> dict:
    out: dict = {}
    if trade_log is None or len(trade_log) == 0:
        return out
    t = trade_log

    # --- options-specific -------------------------------------------------
    qty_mult = t["quantity"] * t["multiplier"]
    credit = (-t["entry_cost"]).clip(lower=0) * qty_mult   # $ received on credit trades
    gross_credit = credit.sum()
    total_pnl = t["realized_pnl"].sum()
    if gross_credit > 0:
        out["premium_capture"] = total_pnl / gross_credit
        out["worst_trade_over_avg_credit"] = (
            t["realized_pnl"].min() / credit[credit > 0].mean()
        )
    days_in_trade = t["days_held"].clip(lower=1)
    out["pnl_per_day_in_trade"] = total_pnl / days_in_trade.sum()

    # exposure: fraction of calendar days in the window with an open position
    entries = pd.to_datetime(t["entry_date"])
    exits = pd.to_datetime(t["exit_date"])
    window_days = max((exits.max() - entries.min()).days, 1)
    open_days = pd.DatetimeIndex([])
    for e, x in zip(entries, exits):
        open_days = open_days.union(pd.date_range(e, x))
    out["exposure"] = len(open_days) / window_days

    # return on margin (short puts; needs strike from description + spot)
    if features is not None and "description" in t.columns:
        spot = features.set_index("quote_date")["spx_close"]
        strikes = t["description"].map(_parse_strike)
        entry_spots = entries.map(spot)
        ok = strikes.notna() & entry_spots.notna()
        if ok.any():
            credit_per_contract = credit[ok] / t.loc[ok, "quantity"]
            margins = pd.Series([
                _short_put_margin(s, k, c) * q
                for s, k, c, q in zip(entry_spots[ok], strikes[ok],
                                      credit_per_contract, t.loc[ok, "quantity"])
            ], index=t.index[ok])
            rom = t.loc[ok, "realized_pnl"] / margins
            out["avg_return_on_margin"] = rom.mean()
            years = max(window_days / 365.25, 1e-9)
            out["ann_return_on_margin"] = (
                total_pnl / margins.mean() / years if margins.mean() > 0 else float("nan")
            )

    # --- statistical honesty ----------------------------------------------
    pnl = t["realized_pnl"]
    out["trade_pnl_tstat"] = float(
        pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))
    ) if len(pnl) > 2 and pnl.std(ddof=1) > 0 else float("nan")
    out["trade_pnl_skew"] = float(pnl.skew())
    out["trade_pnl_kurtosis"] = float(pnl.kurtosis())

    eq = daily_equity(equity_curve)
    if len(eq) > 10:
        rets = eq.pct_change().dropna()
        years = max(len(eq) / 365.25, 1e-9)
        out["cagr"] = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        # vol/PSR on trading days only (calendar ffill inserts flat weekends)
        trd = rets[rets.index.dayofweek < 5]
        out["ann_vol"] = float(trd.std(ddof=1) * math.sqrt(TRADING_DAYS))
        out["probabilistic_sharpe"] = probabilistic_sharpe(trd)
        # drawdown duration
        peak = eq.cummax()
        under = eq < peak
        if under.any():
            groups = (~under).cumsum()[under]
            out["max_dd_days"] = int(groups.value_counts().max())
        else:
            out["max_dd_days"] = 0

        # --- benchmark-relative -------------------------------------------
        if benchmark_close is not None and len(benchmark_close) > 10:
            b = benchmark_close.copy()
            b.index = pd.to_datetime(b.index)
            b = b.resample("D").ffill()
            joint = pd.concat([eq, b], axis=1, join="inner").dropna()
            if len(joint) > 30:
                sr, br = (joint.iloc[:, 0].pct_change().dropna(),
                          joint.iloc[:, 1].pct_change().dropna())
                sr, br = sr.align(br, join="inner")
                mask = sr.index.dayofweek < 5
                sr, br = sr[mask], br[mask]
                if br.std(ddof=1) > 0:
                    beta = sr.cov(br) / br.var(ddof=1)
                    out[f"corr_{benchmark_name}"] = float(sr.corr(br))
                    out[f"beta_{benchmark_name}"] = float(beta)
                    out[f"alpha_ann_{benchmark_name}"] = float(
                        (sr.mean() - beta * br.mean()) * TRADING_DAYS
                    )
                byears = max(len(joint) / 365.25, 1e-9)
                bench_cagr = (joint.iloc[-1, 1] / joint.iloc[0, 1]) ** (1 / byears) - 1
                out[f"excess_cagr_{benchmark_name}"] = out["cagr"] - float(bench_cagr)

    return {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
            for k, v in out.items()}
