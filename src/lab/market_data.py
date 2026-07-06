"""Daily market data (SPX, VIX) from Yahoo Finance with a local parquet cache.

Output schema matches optopsy's stock-data convention so signal dates join the
option chains directly: underlying_symbol, quote_date, open, high, low, close,
volume. The S&P index is stored under symbol "SPX" (same as the chains).
"""

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import CHAIN_DIR, MARKET_DIR

YAHOO_TICKERS = {
    "SPX": "^GSPC",
    "VIX": "^VIX",
    "VIX3M": "^VIX3M",   # 3-month VIX -> term structure slope
    "PUT": "^PUT",       # CBOE PutWrite index -> strategy benchmark
    "BXM": "^BXM",       # CBOE BuyWrite index -> strategy benchmark
}
DEFAULT_START = "2009-01-01"  # one year of warm-up before the 2010 chain data


def _cache_path(symbol: str) -> Path:
    return MARKET_DIR / f"{symbol.lower()}_daily.parquet"


def _download(symbol: str, start: str = DEFAULT_START) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(YAHOO_TICKERS[symbol], start=start, auto_adjust=False, progress=False)
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo Finance returned no data for {symbol}")
    if isinstance(raw.columns, pd.MultiIndex):  # yfinance >=0.2 returns (field, ticker)
        raw.columns = raw.columns.get_level_values(0)
    out = (
        raw.reset_index()
        .rename(columns=str.lower)
        .rename(columns={"date": "quote_date"})
        [["quote_date", "open", "high", "low", "close", "volume"]]
    )
    out.insert(0, "underlying_symbol", symbol)
    out["quote_date"] = pd.to_datetime(out["quote_date"]).dt.tz_localize(None).dt.normalize()
    return out


def load_daily(symbol: str, refresh: bool = False, max_age_days: int = 7) -> pd.DataFrame:
    """Load cached daily OHLCV for SPX or VIX, downloading if missing or stale."""
    path = _cache_path(symbol)
    if not refresh and path.exists():
        cached = pd.read_parquet(path)
        age = date.today() - cached["quote_date"].max().date()
        if age <= timedelta(days=max_age_days):
            return cached
    MARKET_DIR.mkdir(parents=True, exist_ok=True)
    try:
        fresh = _download(symbol)
    except Exception:
        if path.exists():  # offline fallback: stale cache beats no data
            return pd.read_parquet(path)
        if symbol == "SPX":
            return spx_from_chains()
        raise
    fresh.to_parquet(path, index=False)
    return fresh


def spx_from_chains(chain_dir: Path = CHAIN_DIR) -> pd.DataFrame:
    """Fallback SPX daily closes derived from underlying_price in the chain data."""
    files = sorted(chain_dir.glob("*.parquet"))
    if not files:
        raise SystemExit(f"No chain parquets in {chain_dir}")
    frames = [
        pd.read_parquet(f, columns=["quote_date", "underlying_price"]) for f in files
    ]
    px = (
        pd.concat(frames, ignore_index=True)
        .groupby("quote_date", as_index=False)["underlying_price"].last()
        .rename(columns={"underlying_price": "close"})
        .sort_values("quote_date", ignore_index=True)
    )
    px.insert(0, "underlying_symbol", "SPX")
    for col in ("open", "high", "low"):
        px[col] = px["close"]
    px["volume"] = 0
    return px[["underlying_symbol", "quote_date", "open", "high", "low", "close", "volume"]]


def load_market(refresh: bool = False) -> pd.DataFrame:
    """SPX daily bars with VIX (`vix`) and VIX3M (`vix3m`) closes joined on quote_date."""
    spx = load_daily("SPX", refresh=refresh)
    vix = load_daily("VIX", refresh=refresh)[["quote_date", "close"]].rename(columns={"close": "vix"})
    out = spx.merge(vix, on="quote_date", how="left")
    try:
        vix3m = load_daily("VIX3M", refresh=refresh)[["quote_date", "close"]].rename(
            columns={"close": "vix3m"})
        out = out.merge(vix3m, on="quote_date", how="left")
    except Exception:   # term structure is optional; features degrade gracefully
        out["vix3m"] = float("nan")
    return out


def load_benchmark(symbol: str = "PUT", refresh: bool = False) -> Optional[pd.Series]:
    """Benchmark close series (datetime-indexed) for tearsheets/metrics, or None."""
    try:
        df = load_daily(symbol, refresh=refresh)
    except Exception:
        return None
    return df.set_index("quote_date")["close"].rename(symbol)


if __name__ == "__main__":
    df = load_market(refresh=True)
    print(df.tail())
    print(f"{len(df):,} rows, {df['quote_date'].min():%Y-%m-%d} to {df['quote_date'].max():%Y-%m-%d}")
