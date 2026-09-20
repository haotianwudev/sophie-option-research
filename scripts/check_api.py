"""Contract + cross-check for the viewer API against the live results store.

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/check_api.py

Read-only. Uses FastAPI's in-process test client, so no server needs to be running. The
notebooks/pandas are the source of truth: API numbers are compared with the store read
directly, not with what the API itself says.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from lab.api.app import app
from lab import MARKET_DIR
from lab.api import readmodel as rm

c = TestClient(app)
fails: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def get(url, **kw):
    r = c.get(url, **kw)
    if r.status_code == 200:
        json.loads(r.text, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))  # no NaN/Infinity
    return r


raw = pd.read_parquet(rm.RUNS_PATH)
SP, W16 = "short_puts", "2016-01-01..2023-12-31"

# -- health / strategies
h = get("/api/health").json()
check("health reports every run in the store", h["n_runs"] == len(raw), str(h))
st = {s["id"]: s for s in get("/api/strategies").json()}
check("strategy run counts match store",
      st[SP]["n_runs"] == (raw.strategy == SP).sum() and st["iron_condor"]["n_runs"] == (raw.strategy == "iron_condor").sum())

# -- provenance: sim + data source recovered by re-hashing; must cover every run, never guess
src = h["data_sources"]
check("every run's data source is proven by its config hash (none unknown)",
      "unknown" not in src and sum(src.values()) == len(raw), str(src))
# 102 runs pre-date the data_source field (legacy). Every run made since is unified and can grow over time.
check("the 102 pre-2026-09-12 runs are legacy and every later run is unified",
      src.get("legacy") == 102 and src.get("unified") == len(raw) - 102, str(src))
KEYS = ["strategy", "tag", "start", "end", "entry_filter", "params_json"]
tw = raw[raw.duplicated(KEYS, keep=False)]
pairs_differ = all(len({get(f"/api/runs/{x}").json()["data_source"] for x in g.config_hash}) == 2
                   for _, g in tw.groupby(KEYS))
check("in every duplicate pair the two runs differ by data source (legacy vs unified)", pairs_differ)

# -- study shapes (verification 5)
studies = {(s["tag"], s["window"]): s for s in get(f"/api/strategies/{SP}/studies").json()}
kind = lambda t, w: studies[(t, w)]["kind"]
check("sweep04 is a grid", kind("sweep04", W16) == "grid")
check("mgmt04 is a grid", kind("mgmt04", "2022-06-01..2023-12-31") == "grid")
check("optuna04 is a scatter", kind("optuna04", W16) == "scatter")
check("wf_oos is ONE walk-forward study of 10", kind("wf_oos", "walk-forward") == "walk_forward"
      and studies[("wf_oos", "walk-forward")]["n_runs"] == 10)
check("study run counts sum to the strategy total", sum(s["n_runs"] for s in studies.values()) == st[SP]["n_runs"])

# -- grid pivot vs the store read directly (verification 4)
g = get(f"/api/strategies/{SP}/studies/sweep04/{W16}/grid",
        params=[("x", "leg1_delta.target"), ("y", "take_profit"), ("metric", "sharpe_ratio")]).json()
sub = raw[(raw.tag == "sweep04") & (raw.start == "2016-01-01")].copy()
sub["dt"] = [json.loads(p)["leg1_delta"]["target"] for p in sub.params_json]
sub["tp"] = [json.loads(p).get("take_profit") for p in sub.params_json]
sub["sl"] = [json.loads(p).get("stop_loss") for p in sub.params_json]
pin = g["fixed"]["stop_loss"]
sub = sub[sub.sl.map(lambda v: rm.label(v)) == pin]
bad = 0
for cell in g["cells"]:
    m = sub[(sub.dt.map(rm.label) == cell["x"]) & (sub.tp.map(rm.label) == cell["y"])]
    want = m.sharpe_ratio.iloc[0] if len(m) else None
    if cell["ran"] != bool(len(m)) or (want is not None and not np.isclose(cell["value"], want)):
        bad += 1
check("every sweep04 grid cell equals sharpe_ratio read straight from runs.parquet", bad == 0, f"{bad} cells differ")
check("sweep04 grid pins the free third param and says so", "stop_loss" in g["fixed"])
check("all sweep04 cells were run (a true lattice)", g["n_cells_run"] == g["n_cells"] == 6)

# -- scatter
sc = get(f"/api/strategies/{SP}/studies/optuna04/{W16}/grid").json()
check("optuna04 returns points, not cells", sc["kind"] == "scatter" and len(sc["points"]) == 36 and "cells" not in sc)
check("optuna04 does not pin its categorical filter", "entry_filter" not in sc["fixed"])

# -- run detail vs store (verification 4)
hh = raw[(raw.tag == "sweep04")].config_hash.iloc[0]
r = get(f"/api/runs/{hh}").json()
row = raw[raw.config_hash == hh].iloc[0]
check("run metrics equal the stored row", all(
    (r["metrics"][m] is None and pd.isna(row[m])) or np.isclose(r["metrics"][m], row[m])
    for m in r["metrics"]))
check("run says sim/data_source are not recorded", r["not_recorded_in_store"] == ["sim", "data_source"])
check("no NaN left in the 13 backfilled metrics for any run", raw[[
    "premium_capture", "cagr", "probabilistic_sharpe", "max_dd_days"]].notna().all().all())

# -- duplicates are surfaced, not resolved (quirk: same params, two hashes, different results)
dups = raw[raw.duplicated(["strategy", "tag", "start", "end", "entry_filter", "params_json"], keep=False)]
if len(dups):
    d = dups.iloc[0].config_hash
    rd = get(f"/api/runs/{d}").json()
    check("duplicate-params run lists its twin", len(rd["same_params_other_hashes"]) >= 1)
    bl = get(f"/api/strategies/{SP}/studies/baseline/{W16}/params").json()
    check("baseline is a filter_set (no numeric grid)", bl["kind"] == "filter_set")

# -- trades / equity / regimes
t = get(f"/api/runs/{hh}/trades", params={"limit": 5}).json()
check("trades paged, strike parsed, exit types summarised",
      len(t["rows"]) == 5 and t["rows"][0]["strike"] is not None and len(t["by_exit_type"]) >= 1)
n_disk = len(pd.read_parquet(rm.TRADES_DIR / f"{hh}.parquet"))
check("trade total equals the parquet row count", t["total"] == n_disk)
e = get(f"/api/runs/{hh}/equity").json()
check("equity is daily with drawdown <= 0", len(e["points"]) > n_disk and max(p["drawdown"] for p in e["points"]) <= 1e-12)
rg = get(f"/api/runs/{hh}/regimes")
check("regimes return real buckets from cached data", rg.status_code == 200 and len(rg.json()["rows"]) >= 2, rg.text[:120])
check("regime bucket labels are readable (no float noise)", all(len(r["bucket"]) < 30 for r in rg.json()["rows"]), str([r["bucket"] for r in rg.json()["rows"]]))
check("capital inferred as 100000 and matches equity - cumulative_pnl", e["capital_inferred"] == 100000.0)

# -- negative paths (verification 6)
orphans = sorted({p.stem for p in rm.TRADES_DIR.glob("*.parquet")} - set(raw.config_hash))
check("orphan trade logs exist to test with", len(orphans) > 0)
if orphans:
    check("orphan hash -> 404 on run", c.get(f"/api/runs/{orphans[0]}").status_code == 404)
    check("orphan hash -> 404 on trades", c.get(f"/api/runs/{orphans[0]}/trades").status_code == 404)
check("unknown hash -> 404", c.get("/api/runs/deadbeef0000").status_code == 404)
check("empty tag= filters to the empty tag (0 runs), NOT all runs",
      get("/api/runs", params={"tag": ""}).json() == [] and len(get("/api/runs").json()) > 50)
check("unknown metric -> 400", c.get(f"/api/strategies/{SP}/studies/sweep04/{W16}/grid?metric=nope").status_code == 400)
check("axis that does not vary -> 400", c.get(f"/api/strategies/{SP}/studies/sweep04/{W16}/grid?x=max_entry_dte").status_code == 400)
check("bad fix level -> 400", c.get(f"/api/strategies/{SP}/studies/sweep04/{W16}/grid?fix=stop_loss:99").status_code == 400)
check("memo for an unknown tag -> 404", c.get("/api/studies/nope/memo").status_code == 404)
check("unknown study -> 404", c.get(f"/api/strategies/{SP}/studies/nope/x/params").status_code == 404)
lo = get(f"/api/strategies/{SP}/studies/mgmt04/2022-06-01..2023-12-31/grid", params=[("x", "exit_dte")]).json()
check("single-axis request on a 2-param study still returns a grid", lo["kind"] == "grid")
check("filter_set study has no grid view", c.get(f"/api/strategies/{SP}/studies/vrp09/2022-01-01..2023-12-31/grid").status_code == 400)

# -- data availability monitor -------------------------------------------------------------------------------
import glob
av_raw = get("/api/data/availability")
check("availability endpoint responds with strict JSON", av_raw.status_code == 200, av_raw.text[:100])
av = av_raw.json()
files = sorted(glob.glob(str(rm.RESULTS_DIR.parent.parent / "sophie-pipeline" / "data" / "spx_chain_unified" / "year=*" / "month=*" / "day=*" / "chain.parquet")))
check("archive file count equals an independent glob", av["archive"]["n_files"] == len(files), f"{av['archive']['n_files']} vs {len(files)}")
day = lambda f: "-".join(part.split("=")[1] for part in f.replace("\\", "/").split("/")[-4:-1])
have = {day(f) for f in files}
check("archive last date equals the newest day directory", av["archive"]["last"] == max(have))
check("computed NYSE calendar agrees with every cached SPX price day", av["calendar"]["validated"] is True, str(av["calendar"]))
# INDEPENDENT oracle: the cached SPX prices say which days really traded, with no calendar code involved
px = pd.read_parquet(MARKET_DIR / "spx_daily.parquet", columns=["quote_date"])["quote_date"]
traded = {d.strftime("%Y-%m-%d") for d in px if pd.Timestamp("2010-01-04") <= d <= pd.Timestamp(av["calendar"]["to"])}
lo, hi = "2010-01-04", av["calendar"]["to"]
want_gaps = sorted(d for d in traded if d not in have)
got_gaps = [d for d in av["coverage"]["missing_sessions"] if lo <= d <= hi]
check("reported gaps equal (days SPX traded) minus (days with a chain file), oracle-checked", want_gaps == got_gaps,
      f"{len(want_gaps)} vs {len(got_gaps)}")
want_closed = sorted(d for d in have if lo <= d <= hi and d not in traded)
got_closed = [d for d in av["coverage"]["files_on_non_sessions"] if lo <= d <= hi]
check("files on closed days equal (chain days) minus (days SPX traded), oracle-checked", want_closed == got_closed,
      f"{len(want_closed)} vs {len(got_closed)}")
check("gaps are only reported before the newest file", all(d < av["archive"]["last"] for d in av["coverage"]["missing_sessions"]))
check("freshness lists only sessions after the newest file", all(d > av["freshness"]["latest_chain"] for d in av["freshness"]["sessions_since"])
      and av["freshness"]["lag_sessions"] == len(av["freshness"]["sessions_since"]))
check("year rows add up to the coverage totals", sum(y["expected"] for y in av["years"]) == av["coverage"]["expected_sessions"]
      and sum(y["present"] for y in av["years"]) == av["coverage"]["present_sessions"])
check("source segments tile the archive with no overlap or hole",
      sum(x["days"] for x in av["segments"]) == av["archive"]["n_files"])

# -- read-only + no engine on the browse path
check("API exposes no write verbs", all(m == {"GET", "HEAD"} or m <= {"GET", "HEAD", "OPTIONS"}
      for m in [set(r.methods) for r in app.routes if hasattr(r, "methods") and r.path.startswith("/api")]))
check("browse path never imported optopsy", "optopsy" not in sys.modules)

print(f"\n{len(fails)} failed" if fails else "\nall checks passed")
sys.exit(1 if fails else 0)
