"""Delta-triggered roll simulator for short puts.

optopsy models a trade as one entry and one exit; rolling is *position
management* — the position is watched daily and re-struck when its delta
drifts too far from target:

- **Defensive roll** (|delta| >= threshold): the short strike is being
  tested. Close it, re-sell at the original target delta in a later
  expiration — realizes the loss on the leg but keeps collecting premium
  instead of taking the full stop.
- **Offensive roll** (|delta| <= threshold): the trade won early. Close it,
  locking most of the profit, and re-sell at target delta to put the
  premium engine back to work instead of sitting on a near-worthless short.

A *campaign* is the chain of legs from first entry until management stops
(dte <= exit_dte with no rolls left, or max_rolls exhausted). Campaign P&L
is the sum of leg P&Ls — that's what gets compared against the no-roll
baseline.

All quotes are EOD mids; triggers are evaluated once per day at the close,
which matches the chain data's granularity.
"""

from dataclasses import dataclass, field, replace as dc_replace
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class RollConfig:
    target_delta: float = 0.30      # |delta| sold at entry and after every roll
    entry_dte: int = 45             # target DTE at entry / re-strike
    dte_tolerance: int = 12         # accept expirations within +/- this of entry_dte
    exit_dte: int = 21              # stop managing when the held leg reaches this DTE
    defensive_delta: Optional[float] = None   # roll when |delta| >= this (e.g. 0.50)
    offensive_delta: Optional[float] = None   # roll when |delta| <= this (e.g. 0.12)
    time_roll_dte: Optional[int] = None       # tastylive-style: roll at this DTE...
    time_roll_same_strike: bool = True        # ...keeping the strike (pure time roll)
    max_rolls: int = 4              # per campaign, all kinds combined
    min_bid: float = 0.05           # don't sell quotes with no real market

    def label(self) -> str:
        parts = []
        if self.defensive_delta is not None:
            parts.append(f"def@{self.defensive_delta}")
        if self.offensive_delta is not None:
            parts.append(f"off@{self.offensive_delta}")
        if self.time_roll_dte is not None:
            parts.append(f"time@{self.time_roll_dte}dte")
        return "+".join(parts) if parts else "no-roll"


# ---------------------------------------------------------------------------
# Chain access helpers
# ---------------------------------------------------------------------------


def _prepare_puts(chains: pd.DataFrame) -> pd.DataFrame:
    """Puts only, with mid price, indexed for fast (date, expiration, strike) lookup."""
    puts = chains[chains["option_type"] == "p"].copy()
    puts["mid"] = (puts["bid"] + puts["ask"]) / 2
    puts["abs_delta"] = puts["delta"].abs()
    puts = puts.set_index(["quote_date", "expiration", "strike"]).sort_index()
    return puts


def _select_short_put(day_chain: pd.DataFrame, cfg: RollConfig, quote_date) -> Optional[pd.Series]:
    """Pick the put nearest cfg.target_delta with DTE near cfg.entry_dte."""
    dte = (day_chain.index.get_level_values("expiration") - quote_date).days
    ok = (
        (dte >= cfg.entry_dte - cfg.dte_tolerance)
        & (dte <= cfg.entry_dte + cfg.dte_tolerance)
        & (day_chain["bid"] >= cfg.min_bid)
        & day_chain["abs_delta"].notna()
    )
    cands = day_chain[ok]
    if cands.empty:
        return None
    return cands.iloc[(cands["abs_delta"] - cfg.target_delta).abs().argmin()]


def _select_same_strike(
    day_chain: pd.DataFrame, cfg: RollConfig, quote_date, strike: float
) -> Optional[pd.Series]:
    """Pure time roll: same strike, expiration nearest cfg.entry_dte out."""
    exp_idx = day_chain.index.get_level_values("expiration")
    k_idx = day_chain.index.get_level_values("strike")
    dte = (exp_idx - quote_date).days
    ok = (
        (k_idx == strike)
        & (dte >= cfg.entry_dte - cfg.dte_tolerance)
        & (dte <= cfg.entry_dte + cfg.dte_tolerance)
        & (day_chain["bid"] >= cfg.min_bid)
    )
    cands = day_chain[ok]
    if cands.empty:
        return None
    cand_dte = (cands.index.get_level_values("expiration") - quote_date).days
    return cands.iloc[np.abs(cand_dte - cfg.entry_dte).argmin()]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def simulate_campaigns(
    chains: pd.DataFrame,
    cfg: RollConfig,
    entry_dates: Optional[list] = None,
    entry_frequency: str = "MS",   # monthly campaign starts by default
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run roll campaigns; returns (legs, campaigns) DataFrames.

    legs: one row per short-put leg with open/close details and the reason
    the leg ended (defensive_roll / offensive_roll / exit / expiry).
    campaigns: one row per campaign with roll counts and total P&L.
    """
    puts = _prepare_puts(chains)
    trading_dates = puts.index.get_level_values("quote_date").unique().sort_values()
    if entry_dates is None:
        wanted = pd.date_range(trading_dates.min(), trading_dates.max(), freq=entry_frequency)
        idx = trading_dates.searchsorted(wanted)
        entry_dates = trading_dates[idx[idx < len(trading_dates)]].unique()

    legs_rows, camp_rows = [], []
    for camp_id, entry_date in enumerate(entry_dates):
        result = _run_campaign(puts, trading_dates, cfg, entry_date, camp_id)
        if result is None:
            continue
        legs, camp = result
        legs_rows.extend(legs)
        camp_rows.append(camp)

    legs_df = pd.DataFrame(legs_rows)
    camp_df = pd.DataFrame(camp_rows)
    return legs_df, camp_df


def _day_quote(puts: pd.DataFrame, date, expiration, strike) -> Optional[pd.Series]:
    try:
        row = puts.loc[(date, expiration, strike)]
    except KeyError:
        return None
    return row.iloc[0] if isinstance(row, pd.DataFrame) else row


def _run_campaign(puts, trading_dates, cfg: RollConfig, entry_date, camp_id):
    try:
        day_chain = puts.loc[entry_date]
    except KeyError:
        return None
    pick = _select_short_put(day_chain, cfg, entry_date)
    if pick is None:
        return None

    legs = []
    rolls_used = n_def = n_off = n_time = 0
    max_abs_delta = pick["abs_delta"]

    # current leg state
    expiration, strike = pick.name[0], pick.name[1]
    open_date, open_mid, open_delta = entry_date, pick["mid"], pick["abs_delta"]

    date_pos = trading_dates.searchsorted(entry_date) + 1
    last_quote = pick

    while True:
        if date_pos >= len(trading_dates):
            # data ended with the leg open: close at last seen quote
            legs.append(_leg(camp_id, open_date, trading_dates[-1], expiration, strike,
                             open_mid, last_quote["mid"], open_delta, last_quote["abs_delta"],
                             "data_end"))
            break
        date = trading_dates[date_pos]

        if date >= expiration:
            # expired: settle at intrinsic using last known underlying
            intrinsic = max(strike - last_quote.get("underlying_price", strike), 0.0)
            legs.append(_leg(camp_id, open_date, expiration, expiration, strike,
                             open_mid, intrinsic, open_delta, last_quote["abs_delta"], "expiry"))
            break

        q = _day_quote(puts, date, expiration, strike)
        if q is None:               # no quote today; carry position
            date_pos += 1
            continue
        last_quote = q
        abs_delta = q["abs_delta"]
        if pd.notna(abs_delta):
            max_abs_delta = max(max_abs_delta, abs_delta)
        dte = (expiration - date).days

        reason = None
        if cfg.time_roll_dte is not None and dte <= cfg.time_roll_dte:
            reason = "time_roll" if rolls_used < cfg.max_rolls else "exit"
        elif dte <= cfg.exit_dte:
            reason = "exit"
        elif (cfg.defensive_delta is not None and pd.notna(abs_delta)
              and abs_delta >= cfg.defensive_delta and rolls_used < cfg.max_rolls):
            reason = "defensive_roll"
        elif (cfg.offensive_delta is not None and pd.notna(abs_delta)
              and abs_delta <= cfg.offensive_delta and rolls_used < cfg.max_rolls):
            reason = "offensive_roll"

        if reason is None:
            date_pos += 1
            continue

        legs.append(_leg(camp_id, open_date, date, expiration, strike,
                         open_mid, q["mid"], open_delta, abs_delta, reason))
        if reason == "exit":
            break

        # roll: open a fresh leg ~entry_dte out, same day
        rolls_used += 1
        n_def += reason == "defensive_roll"
        n_off += reason == "offensive_roll"
        n_time += reason == "time_roll"
        day_chain = puts.loc[date]
        if reason == "time_roll" and cfg.time_roll_same_strike:
            # pure time roll keeps the strike; fall back to delta targeting
            # if that strike isn't quoted in the target expiration window
            pick = (_select_same_strike(day_chain, cfg, date, strike)
                    if strike is not None else None)
            if pick is None:
                pick = _select_short_put(day_chain, cfg, date)
        else:
            pick = _select_short_put(day_chain, cfg, date)
        if pick is None:            # nothing to roll into; campaign ends here
            break
        expiration, strike = pick.name[0], pick.name[1]
        open_date, open_mid, open_delta = date, pick["mid"], pick["abs_delta"]
        last_quote = pick
        date_pos += 1

    if not legs:
        return None
    total_pnl = sum(l["pnl"] for l in legs)
    first, last = legs[0], legs[-1]
    camp = {
        "campaign_id": camp_id,
        "entry_date": first["open_date"],
        "exit_date": last["close_date"],
        "days": (last["close_date"] - first["open_date"]).days,
        "n_legs": len(legs),
        "n_defensive": n_def,
        "n_offensive": n_off,
        "n_time": n_time,
        "total_credit": sum(l["open_mid"] for l in legs),
        "total_pnl": total_pnl,
        "max_abs_delta": max_abs_delta,
        "entry_strike": first["strike"],
        "win": total_pnl > 0,
    }
    return legs, camp


def _leg(camp_id, open_date, close_date, expiration, strike,
         open_mid, close_mid, open_delta, close_delta, reason) -> dict:
    return {
        "campaign_id": camp_id,
        "open_date": open_date,
        "close_date": close_date,
        "expiration": expiration,
        "strike": strike,
        "open_mid": open_mid,
        "close_mid": close_mid,
        "open_abs_delta": open_delta,
        "close_abs_delta": close_delta,
        "close_reason": reason,
        # short leg: sold at open_mid, bought back at close_mid
        "pnl": (open_mid - close_mid) * 100,
    }


# ---------------------------------------------------------------------------
# Variant comparison
# ---------------------------------------------------------------------------


def compare_variants(
    chains: pd.DataFrame,
    base: Optional[RollConfig] = None,
    defensive: float = 0.50,
    offensive: float = 0.12,
    entry_dates: Optional[list] = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Run no-roll / defensive / offensive / both on identical entries.

    Returns (summary table, {variant: campaigns df}).
    """
    base = base or RollConfig()
    variants = {
        "no-roll": base,
        "defensive": dc_replace(base, defensive_delta=defensive),
        "offensive": dc_replace(base, offensive_delta=offensive),
        "both": dc_replace(base, defensive_delta=defensive, offensive_delta=offensive),
    }
    rows, campaigns = [], {}
    for name, cfg in variants.items():
        _, camp = simulate_campaigns(chains, cfg, entry_dates=entry_dates)
        campaigns[name] = camp
        rows.append(summarize_campaigns(camp, name))
    return pd.DataFrame(rows).set_index("variant"), campaigns


def summarize_campaigns(camp: pd.DataFrame, name: str = "") -> dict:
    if camp.empty:
        return {"variant": name, "campaigns": 0}
    cum = camp.sort_values("exit_date")["total_pnl"].cumsum()
    dd = (cum - cum.cummax()).min()
    return {
        "variant": name,
        "campaigns": len(camp),
        "win_rate": camp["win"].mean(),
        "avg_pnl": camp["total_pnl"].mean(),
        "total_pnl": camp["total_pnl"].sum(),
        "worst_campaign": camp["total_pnl"].min(),
        "max_dd_$": dd,
        "avg_rolls": (camp["n_defensive"] + camp["n_offensive"]
                      + camp.get("n_time", 0)).mean(),
        "avg_days": camp["days"].mean(),
        "pnl_per_day": camp["total_pnl"].sum() / max(camp["days"].sum(), 1),
    }


if __name__ == "__main__":
    from .backtest import load_chains

    chains = load_chains("2022-01-01", "2023-12-31")
    summary, camps = compare_variants(chains)
    print(summary.round(3).to_string())
    both = camps["both"]
    print(f"\nroll counts (both): defensive={both.n_defensive.sum()}, "
          f"offensive={both.n_offensive.sum()} over {len(both)} campaigns")
