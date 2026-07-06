"""Position sizing overlays applied to a completed trade log.

Sizing is studied as a post-processing step (as in the arXiv put-writing
sizing study): the entry/exit decisions stay identical; only the number of
contracts per trade changes. That isolates the sizing effect from strategy
effects. All estimators use only data available before each trade's entry.
"""

from typing import Optional

import numpy as np
import pandas as pd


def fixed(trade_log: pd.DataFrame) -> pd.Series:
    """1 contract per trade — the baseline."""
    return pd.Series(1.0, index=trade_log.index)


def vix_scaled(
    trade_log: pd.DataFrame,
    features: pd.DataFrame,
    target_vix: float = 20.0,
    max_contracts: float = 3.0,
) -> pd.Series:
    """Inverse-VIX sizing: risk less when vol is high (target_vix / vix).

    The classic vol-targeting overlay: position notional shrinks exactly when
    the tails get fat. Capped to avoid silly size in dead-calm markets.
    """
    vix = features.set_index("quote_date")["vix"]
    entry_vix = pd.to_datetime(trade_log["entry_date"]).map(vix)
    return (target_vix / entry_vix).clip(upper=max_contracts).fillna(1.0)


def kelly_fraction(
    trade_log: pd.DataFrame,
    lookback: int = 50,
    fraction: float = 0.5,
    max_contracts: float = 3.0,
) -> pd.Series:
    """Fractional Kelly from a rolling window of *prior* trades.

    f* = p - (1-p)/b with p = rolling win rate, b = rolling avg-win/avg-loss.
    Scaled by `fraction` (half-Kelly default) and normalized so the average
    size ~1 contract, making totals comparable with the fixed baseline.
    """
    pnl = trade_log["realized_pnl"]
    wins = (pnl > 0).astype(float)
    p = wins.shift(1).rolling(lookback, min_periods=20).mean()
    avg_win = pnl.where(pnl > 0).shift(1).rolling(lookback * 2, min_periods=10).mean()
    avg_loss = (-pnl.where(pnl < 0)).shift(1).rolling(lookback * 2, min_periods=10).mean()
    b = (avg_win / avg_loss).replace([np.inf, -np.inf], np.nan)
    f = (p - (1 - p) / b) * fraction
    f = f.clip(lower=0.0, upper=None)
    scale = f.mean()
    sized = (f / scale if scale and scale > 0 else f).clip(upper=max_contracts)
    return sized.fillna(1.0)


def apply_sizing(
    trade_log: pd.DataFrame,
    contracts: pd.Series,
    capital: float = 100_000.0,
) -> dict:
    """Scale each trade's P&L by its contract count and summarize."""
    pnl = trade_log["realized_pnl"] * contracts
    eq = capital + pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    return {
        "total_pnl": pnl.sum(),
        "avg_pnl": pnl.mean(),
        "win_rate": (pnl > 0).mean(),
        "worst_trade": pnl.min(),
        "max_dd_$": dd,
        "return_over_maxdd": pnl.sum() / abs(dd) if dd < 0 else np.nan,
        "avg_contracts": contracts.mean(),
    }


def compare_sizing(
    trade_log: pd.DataFrame,
    features: Optional[pd.DataFrame] = None,
    capital: float = 100_000.0,
) -> pd.DataFrame:
    """Fixed vs VIX-scaled vs fractional-Kelly on the same trade log."""
    methods = {"fixed_1lot": fixed(trade_log)}
    if features is not None:
        methods["vix_scaled"] = vix_scaled(trade_log, features)
    methods["half_kelly"] = kelly_fraction(trade_log)
    rows = {name: apply_sizing(trade_log, c, capital) for name, c in methods.items()}
    return pd.DataFrame(rows).T
