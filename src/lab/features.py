"""Daily feature matrix shared by entry-signal filters and ML models.

One row per trading day, keyed by quote_date. Every feature only uses
information available at that day's close (no lookahead) — a trade entered
on day T is conditioned on features computed through T's close, matching
EOD chain quotes.
"""

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI, matching the optopsy implementation (ewm alpha=1/period)."""
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period).mean()
    rs = gain / loss
    return 100 - 100 / (1 + rs)


def rolling_percentile(series: pd.Series, window: int = TRADING_DAYS) -> pd.Series:
    """Percentile rank of the latest value within the trailing window (0..1)."""
    return series.rolling(window, min_periods=window // 2).apply(
        lambda w: (w <= w[-1]).mean(), raw=True
    )


def build_features(market: pd.DataFrame) -> pd.DataFrame:
    """Compute the feature matrix from load_market() output (SPX bars + vix column)."""
    df = market.sort_values("quote_date").reset_index(drop=True)
    close, vix = df["close"], df["vix"]

    log_ret = np.log(close / close.shift(1))
    realized_vol = log_ret.rolling(20).std() * np.sqrt(TRADING_DAYS) * 100

    feats = pd.DataFrame({
        "quote_date": df["quote_date"],
        "spx_close": close,
        "vix": vix,
        "vix_rank": rolling_percentile(vix),           # 1y percentile of VIX
        "vix_chg_5d": vix.pct_change(5),
        "rsi14": rsi(close),
        "realized_vol_20d": realized_vol,
        "vol_risk_premium": vix - realized_vol,        # implied minus realized
        "ret_5d": close.pct_change(5),
        "ret_21d": close.pct_change(21),
        "sma50_above_sma200": (
            close.rolling(50).mean() > close.rolling(200).mean()
        ).astype(int),
        "dist_from_high_52w": close / close.rolling(TRADING_DAYS).max() - 1,
        "day_of_week": df["quote_date"].dt.dayofweek,
    })
    return feats


# Columns used as ML model inputs (excludes keys and raw price levels)
ML_FEATURES = [
    "vix", "vix_rank", "vix_chg_5d", "rsi14", "realized_vol_20d",
    "vol_risk_premium", "ret_5d", "ret_21d", "sma50_above_sma200",
    "dist_from_high_52w", "day_of_week",
]


def entry_dates_from_expr(features: pd.DataFrame, expr: str) -> pd.DataFrame:
    """Evaluate a filter expression against the feature matrix and return the
    (underlying_symbol, quote_date) pairs optopsy strategies accept as entry_dates.

    Example expr: "vix_rank > 0.5 and rsi14 < 40"
    """
    mask = features.eval(expr)
    dates = features.loc[mask.fillna(False), ["quote_date"]].copy()
    dates.insert(0, "underlying_symbol", "SPX")
    return dates.reset_index(drop=True)


if __name__ == "__main__":
    from .market_data import load_market

    feats = build_features(load_market())
    print(feats.tail())
    # Sanity: March 2020 must show extreme vol features
    covid = feats[(feats.quote_date >= "2020-03-15") & (feats.quote_date <= "2020-03-25")]
    print("\nCOVID spike check (expect vix > 60, vix_rank ~1.0, rsi14 < 35):")
    print(covid[["quote_date", "vix", "vix_rank", "rsi14", "realized_vol_20d"]].to_string(index=False))
