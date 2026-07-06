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


def ewm_z_score(series: pd.Series, span: int = 126, min_periods: int = 63) -> pd.Series:
    """EWM z-score (same normalization pattern the sophie-pipeline agents use)."""
    ewm_mean = series.ewm(span=span, min_periods=min_periods, ignore_na=True).mean()
    ewm_std = series.ewm(span=span, min_periods=min_periods, ignore_na=True).std()
    return (series - ewm_mean) / ewm_std.replace(0, np.nan)


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
    # VRP z-score: is today's implied-minus-realized spread rich or thin vs
    # its own recent history? (AQR-style VRP timing signal)
    feats["vrp_z"] = ewm_z_score(feats["vol_risk_premium"])
    # Term structure: VIX3M - VIX. Positive = contango (normal); negative =
    # backwardation (stress). Needs vix3m from load_market(); NaN if missing.
    if "vix3m" in df.columns:
        feats["term_slope"] = df["vix3m"] - vix
    else:
        feats["term_slope"] = np.nan
    return feats


# Columns used as ML model inputs (excludes keys and raw price levels)
ML_FEATURES = [
    "vix", "vix_rank", "vix_chg_5d", "rsi14", "realized_vol_20d",
    "vol_risk_premium", "vrp_z", "term_slope", "ret_5d", "ret_21d",
    "sma50_above_sma200", "dist_from_high_52w", "day_of_week",
]


# ---------------------------------------------------------------------------
# Chain-native features (need the option chains, not just market data)
# ---------------------------------------------------------------------------


def build_chain_features(chains: pd.DataFrame) -> pd.DataFrame:
    """Daily ATM implied vol and its 1y rank, computed from the chains themselves.

    The processed chains carry no IV column, so ATM IV is backed out from the
    ~30-DTE ATM straddle with the Brenner-Subrahmanyam approximation:
    straddle_mid ~= 0.8 * S * sigma * sqrt(T). Accurate to a few percent ATM —
    plenty for a rank/regime feature (do not use it to price anything).
    """
    df = chains[["quote_date", "expiration", "strike", "option_type",
                 "bid", "ask", "underlying_price"]].copy()
    df["dte"] = (df["expiration"] - df["quote_date"]).dt.days
    df = df[(df["dte"] >= 20) & (df["dte"] <= 45) & (df["bid"] > 0)]
    df["mid"] = (df["bid"] + df["ask"]) / 2

    # nearest-to-30-DTE expiration per day
    df["dte_dist"] = (df["dte"] - 30).abs()
    best_exp = df.loc[df.groupby("quote_date")["dte_dist"].idxmin(),
                      ["quote_date", "expiration"]]
    df = df.merge(best_exp, on=["quote_date", "expiration"])

    # ATM strike per day (nearest to spot)
    df["k_dist"] = (df["strike"] - df["underlying_price"]).abs()
    best_k = df.loc[df.groupby("quote_date")["k_dist"].idxmin(),
                    ["quote_date", "strike"]]
    df = df.merge(best_k, on=["quote_date", "strike"])

    day = df.groupby(["quote_date", "option_type"]).agg(
        mid=("mid", "mean"), spot=("underlying_price", "last"), dte=("dte", "last")
    ).reset_index()
    straddle = day.pivot_table(index="quote_date", values="mid",
                               columns="option_type", aggfunc="mean")
    meta = day.groupby("quote_date")[["spot", "dte"]].last()
    out = straddle.join(meta).dropna(subset=["c", "p"])
    out["atm_iv"] = (
        (out["c"] + out["p"]) / (0.8 * out["spot"] * np.sqrt(out["dte"] / 365.0)) * 100
    )
    out["atm_iv_rank"] = rolling_percentile(out["atm_iv"])
    return out.reset_index()[["quote_date", "atm_iv", "atm_iv_rank"]]


def full_features(market: pd.DataFrame, chains: pd.DataFrame) -> pd.DataFrame:
    """Market features merged with chain-native ATM IV features."""
    return build_features(market).merge(
        build_chain_features(chains), on="quote_date", how="left"
    )


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
