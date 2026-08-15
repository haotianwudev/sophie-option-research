"""One-off export: dump one full real SPX EOD option chain (all expirations, one quote_date)
to JSON, for the Sophie frontend's interactive options payoff builder and options viewer.

Not part of the notebook pipeline — this bundles a single historical snapshot as a static
frontend asset, distinct from the backtest results already published via lab/db.py.

Precomputes each contract's implied vol here (vectorized bisection against Black-Scholes) rather
than leaving it to be solved client-side — IV depends only on the contract's own strike/mid/DTE,
never on anything the user can change in the UI, so it only ever needs solving once. Gamma/vega/
theta/rho are also precomputed here, as a single closed-form evaluation from that same solved IV
(mirrors black-scholes.ts's blackScholes() exactly, including its vega/100, theta/365, rho/100
scaling) — the frontend's live Cloud Run service returns these natively as part of its response
shape, so the historical snapshot needs them too rather than silently defaulting them to 0.

Open interest is SYNTHETIC. OptionsDX's EOD schema has no OPEN_INTEREST column at all (confirmed
against the raw CSV header), so it can't be recovered from real data. `synthetic_open_interest()`
below fabricates plausible values instead, calibrated against a real live SPX chain pulled from
the Cloud Run options-analytics service (2026-08-14) — not random noise. That calibration found:
  - OI is heavy-tailed and only loosely volume-correlated (log-log corr ~0.47, raw corr ~0.03).
  - OI is *not* peaked at the money: median OI actually runs LOWER near-the-money (|delta| 0.6-0.95,
    median 0-16 — positions get closed out/exercised) than far OTM (|delta| <0.05, median 170-215
    — resting hedges/income positions accumulate for months). Deepest ITM decays back to ~0.
  - Strikes divisible by 100 carry ~11x the OI of non-round strikes at similar moneyness; div-by-50
    ~3.4x; div-by-25 ~1.4x (measured within a fixed delta band to isolate the round-number effect
    from the moneyness effect above).
  - Standard monthly (3rd-Friday-of-month) expirations carry ~5-15x the per-contract OI of
    adjacent weeklies (e.g. the Aug 21 2026 monthly: avg 1440 vs ~150-370 on nearby weeklies) —
    they've simply been open longer, accumulating positions the whole time.
This is dummy data for a 2023 sample snapshot, not a claim about actual 2023 open interest.

Usage:
    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/export_spx_chain_sample.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).parent.parent
SOURCE_FILE = ROOT / "data/processed/spx_eod_202312.parquet"
QUOTE_DATE = "2023-12-29"  # most recent trading day available in the local OptionsDX data
OUT_FILE = (
    ROOT.parent
    / "ai-stock-suggestion-client/public/data/spx-chain-sample.json"
)

# Cross-referenced with SPX_DEFAULT_RATE / SPX_DEFAULT_DIV_YIELD in
# ai-stock-suggestion-client/src/lib/options/analytics.ts — keep in sync if either changes.
DEFAULT_RATE = 0.054
DEFAULT_DIV_YIELD = 0.013


def bs_price(S: np.ndarray, K: np.ndarray, T: np.ndarray, r: float, q: float, sigma: np.ndarray, is_call: np.ndarray) -> np.ndarray:
    """Black-Scholes-Merton price with continuous dividend yield — mirrors black-scholes.ts's blackScholes()."""
    sqrt_t = np.sqrt(T)
    d1 = (np.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_q = np.exp(-q * T)
    disc_r = np.exp(-r * T)
    call = S * disc_q * norm.cdf(d1) - K * disc_r * norm.cdf(d2)
    put = K * disc_r * norm.cdf(-d2) - S * disc_q * norm.cdf(-d1)
    return np.where(is_call, call, put)


def solve_iv_vectorized(price: np.ndarray, S: np.ndarray, K: np.ndarray, T: np.ndarray, r: float, q: float,
                         is_call: np.ndarray, valid: np.ndarray, iterations: int = 60) -> np.ndarray:
    """Bisection solve for every row at once (numpy, not a Python loop) — fast across ~18k contracts."""
    lo = np.full(price.shape, 0.001)
    hi = np.full(price.shape, 5.0)
    # Rows outside `valid` (T<=0 or price<=0) still run through the loop harmlessly (bounded
    # inputs avoid div-by-zero blowups propagating as NaN-poisoning neighbors); their result is
    # discarded by the caller via `valid` afterward.
    safe_T = np.where(valid, T, 1.0)
    for _ in range(iterations):
        mid = (lo + hi) / 2
        p = bs_price(S, K, safe_T, r, q, mid, is_call)
        too_high = p > price
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
    return np.where(valid, (lo + hi) / 2, np.nan)


def bs_greeks(S: np.ndarray, K: np.ndarray, T: np.ndarray, r: float, q: float, sigma: np.ndarray,
              is_call: np.ndarray, valid: np.ndarray) -> dict:
    """Closed-form gamma/vega/theta/rho — mirrors black-scholes.ts's blackScholes() scaling exactly
    (vega per 1 vol pt / 100, theta per calendar day / 365, rho per 1 rate pt / 100, gamma unscaled).
    `valid` rows outside it (T<=0, price<=0, or IV failed to solve) get NaN, same convention as iv."""
    safe_T = np.where(valid, T, 1.0)
    safe_sigma = np.where(valid & (sigma > 0), sigma, 0.2)
    sqrt_t = np.sqrt(safe_T)
    d1 = (np.log(S / K) + (r - q + 0.5 * safe_sigma ** 2) * safe_T) / (safe_sigma * sqrt_t)
    d2 = d1 - safe_sigma * sqrt_t
    disc_q = np.exp(-q * safe_T)
    disc_r = np.exp(-r * safe_T)
    pdf_d1 = norm.pdf(d1)

    gamma = disc_q * pdf_d1 / (S * safe_sigma * sqrt_t)
    vega = S * disc_q * pdf_d1 * sqrt_t / 100.0

    call_theta = (-(S * disc_q * pdf_d1 * safe_sigma) / (2 * sqrt_t)
                  - r * K * disc_r * norm.cdf(d2)
                  + q * S * disc_q * norm.cdf(d1)) / 365.0
    put_theta = (-(S * disc_q * pdf_d1 * safe_sigma) / (2 * sqrt_t)
                 + r * K * disc_r * norm.cdf(-d2)
                 - q * S * disc_q * norm.cdf(-d1)) / 365.0
    theta = np.where(is_call, call_theta, put_theta)

    call_rho = K * safe_T * disc_r * norm.cdf(d2) / 100.0
    put_rho = -K * safe_T * disc_r * norm.cdf(-d2) / 100.0
    rho = np.where(is_call, call_rho, put_rho)

    nan = np.where(valid, 1.0, np.nan)
    return {
        "gamma": gamma * nan,
        "vega": vega * nan,
        "theta": theta * nan,
        "rho": rho * nan,
    }


# Piecewise-linear anchors for OI-vs-|delta|, read off the real live-chain calibration (module
# docstring). xp must be increasing for np.interp; fp is the median OI observed at that |delta|.
_OI_DELTA_XP = np.array([0.0, 0.005, 0.03, 0.1, 0.2, 0.325, 0.5, 0.7, 0.875, 0.95, 1.0])
_OI_DELTA_FP = np.array([215, 215, 170, 117, 95, 78, 44, 16, 2, 0, 0], dtype=float)


def is_third_friday(dates: pd.Series) -> np.ndarray:
    """True for standard monthly SPX expirations (3rd Friday of the month)."""
    is_friday = dates.dt.dayofweek == 4
    is_3rd = (dates.dt.day >= 15) & (dates.dt.day <= 21)
    return (is_friday & is_3rd).to_numpy()


def synthetic_open_interest(abs_delta: np.ndarray, strike: np.ndarray, volume: np.ndarray,
                             is_monthly: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Fabricated but calibrated open interest — see module docstring for the reference numbers
    this was fit against. Not real reported OI (OptionsDX's EOD data doesn't have any)."""
    base = np.interp(abs_delta, _OI_DELTA_XP, _OI_DELTA_FP)

    # Round to whole dollars before the modulo check — float strikes (e.g. 4700.0) can otherwise
    # miss an exact `% 100 == 0` due to binary floating-point representation error.
    strike_int = np.round(strike).astype(np.int64)
    round_mult = np.select(
        [strike_int % 100 == 0, strike_int % 50 == 0, strike_int % 25 == 0],
        [6.0, 3.0, 1.4],
        default=1.0,
    )

    vol_mult = 1 + 0.25 * np.log1p(np.nan_to_num(volume, nan=0.0))
    monthly_mult = np.where(is_monthly, 5.0, 1.0)

    # Real OI is heavily right-skewed (a handful of strikes carry 100k+ contracts) — a lognormal
    # multiplier reproduces that shape instead of a flat/normal spread around the base curve.
    noise = rng.lognormal(mean=0.0, sigma=0.6, size=abs_delta.shape)

    oi = base * round_mult * vol_mult * monthly_mult * noise
    return np.clip(np.round(oi), 0, 400_000)


def main() -> None:
    if not SOURCE_FILE.exists():
        sys.exit(f"Missing source file: {SOURCE_FILE}")

    df = pd.read_parquet(SOURCE_FILE)
    day = df[df["quote_date"] == pd.Timestamp(QUOTE_DATE)].copy()
    if day.empty:
        sys.exit(f"No rows for quote_date={QUOTE_DATE} in {SOURCE_FILE}")

    day["mid"] = (day["bid"] + day["ask"]) / 2
    day["dte"] = (day["expiration"] - day["quote_date"]).dt.days
    underlying_price = float(day["underlying_price"].iloc[0])

    # Precompute IV for the whole day's chain at once (vectorized), at the fixed default
    # rate/dividend-yield — see module docstring for why only IV, not Greeks, is precomputed.
    T = day["dte"].to_numpy(dtype=float) / 365.0
    price = day["mid"].to_numpy(dtype=float)
    valid = (T > 0) & (price > 0)
    is_call = (day["option_type"] == "c").to_numpy()
    strikes = day["strike"].to_numpy(dtype=float)
    underlying_arr = np.full(len(day), underlying_price)
    day["iv"] = solve_iv_vectorized(
        price=price,
        S=underlying_arr,
        K=strikes,
        T=T,
        r=DEFAULT_RATE,
        q=DEFAULT_DIV_YIELD,
        is_call=is_call,
        valid=valid,
    )

    # Greeks derived from the solved IV — same validity mask (solve can fail even where T/price
    # were valid inputs, e.g. iv came back NaN from a degenerate bracket).
    greeks_valid = valid & ~np.isnan(day["iv"].to_numpy())
    greeks = bs_greeks(
        S=underlying_arr,
        K=strikes,
        T=T,
        r=DEFAULT_RATE,
        q=DEFAULT_DIV_YIELD,
        sigma=day["iv"].to_numpy(),
        is_call=is_call,
        valid=greeks_valid,
    )
    day["gamma"] = greeks["gamma"]
    day["vega"] = greeks["vega"]
    day["theta"] = greeks["theta"]
    day["rho"] = greeks["rho"]

    # Synthetic (not real — see module docstring) open interest, calibrated against a real live
    # SPX chain. Fixed seed so re-running the export doesn't reshuffle the snapshot's numbers.
    rng = np.random.default_rng(20231229)
    day["openInterest"] = synthetic_open_interest(
        abs_delta=np.abs(day["delta"].to_numpy()),
        strike=strikes,
        volume=day["volume"].to_numpy(dtype=float),
        is_monthly=is_third_friday(day["expiration"]),
        rng=rng,
    )

    expirations = []
    for expiration, group in day.groupby("expiration", sort=True):
        dte = int(group["dte"].iloc[0])

        def contracts(option_type: str):
            sub = group[group["option_type"] == option_type].sort_values("strike")
            return [
                {
                    "strike": float(r.strike),
                    "bid": float(r.bid),
                    "ask": float(r.ask),
                    "mid": round(float(r.mid), 4),
                    "delta": round(float(r.delta), 5),
                    "iv": None if pd.isna(r.iv) else round(float(r.iv), 4),
                    "gamma": None if pd.isna(r.gamma) else round(float(r.gamma), 6),
                    "vega": None if pd.isna(r.vega) else round(float(r.vega), 4),
                    "theta": None if pd.isna(r.theta) else round(float(r.theta), 4),
                    "rho": None if pd.isna(r.rho) else round(float(r.rho), 4),
                    "volume": None if pd.isna(r.volume) else float(r.volume),
                    "openInterest": int(r.openInterest),
                }
                for r in sub.itertuples()
            ]

        expirations.append(
            {
                "expiration": expiration.strftime("%Y-%m-%d"),
                "dte": dte,
                "calls": contracts("c"),
                "puts": contracts("p"),
            }
        )

    snapshot = {
        "symbol": "SPX",
        "quoteDate": QUOTE_DATE,
        "underlyingPrice": underlying_price,
        # Not present in OptionsDX's EOD data — fabricated (see module docstring). Every other
        # field in this snapshot is real.
        "openInterestSynthetic": True,
        "expirations": expirations,
    }

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(snapshot, separators=(",", ":")))

    total_contracts = sum(len(e["calls"]) + len(e["puts"]) for e in expirations)
    print(f"Wrote {OUT_FILE} — {len(expirations)} expirations, {total_contracts} contracts, "
          f"underlying=${underlying_price:.2f}, size={OUT_FILE.stat().st_size / 1024:.0f}KB")


if __name__ == "__main__":
    main()
