"""Tests for run removal/deletion, run against a SCRATCH COPY of the results store -- never the real one.

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/check_removal.py

Copies results/runs.parquet and results/trades/ into a temp directory, points the read model at the copy, and
exercises every path: reversibility, the browser guards, the confirmation rules, the refusal to write while the
store is changing, and a corrupted sidecar file. Ends by proving the real store was not touched.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
from fastapi.testclient import TestClient

from lab.api import readmodel as rm
from lab.api import removal
from lab.api.app import ALLOWED_ORIGINS, MUTATING, app

REAL = Path(__file__).resolve().parents[1] / "results"
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
real_before = (sha(REAL / "runs.parquet"), (REAL / "removed_runs.json").exists())

fails: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{detail}]" if detail and not ok else ""))
    if not ok:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="removal_check_"))
shutil.copy2(REAL / "runs.parquet", tmp / "runs.parquet")
shutil.copytree(REAL / "trades", tmp / "trades")
saved = (rm.RUNS_PATH, rm.TRADES_DIR, rm.REMOVED_PATH)
rm.RUNS_PATH, rm.TRADES_DIR, rm.REMOVED_PATH = tmp / "runs.parquet", tmp / "trades", tmp / "removed_runs.json"
rm.invalidate()

OK = {"Origin": ALLOWED_ORIGINS[1], "X-Viewer-Action": "1"}
c = TestClient(app)
c500 = TestClient(app, raise_server_exceptions=False)
try:
    raw = pd.read_parquet(rm.RUNS_PATH)
    N = len(raw)
    rows = raw[raw.tag == "sweep04"]
    H, H2, H3 = rows.config_hash.iloc[0], rows.config_hash.iloc[1], rows.config_hash.iloc[2]
    row = raw[raw.config_hash == H].iloc[0]
    study = f"/api/strategies/{row.strategy}/studies"
    n_study = lambda: next(s["n_runs"] for s in c.get(study).json() if s["tag"] == row.tag and s["window"] == (row.start + ".." + row.end))
    base_study = n_study()
    parquet_hash = sha(rm.RUNS_PATH)

    # ---- the guards: state changes only from this UI's origin, with the custom header -----------------------------
    check("only the three expected routes mutate, and they are POST",
          sorted((m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods if m not in ("GET", "HEAD", "OPTIONS"))
          == sorted(MUTATING))
    url = f"/api/runs/{H}/remove"
    check("POST without the header is refused (403)", c.post(url, json={}, headers={"Origin": ALLOWED_ORIGINS[1]}).status_code == 403)
    check("POST from a foreign origin is refused (403)", c.post(url, json={}, headers={**OK, "Origin": "http://evil.example"}).status_code == 403)
    check("POST with no Origin is refused (403)", c.post(url, json={}, headers={"X-Viewer-Action": "1"}).status_code == 403)
    check("a refused POST changed nothing", rm.read_tombstones() == [] and sha(rm.RUNS_PATH) == parquet_hash)
    check("malformed hashes are rejected (400), not used as paths",
          all(c.post(f"/api/runs/{bad}/remove", json={}, headers=OK).status_code in (400, 404) for bad in ("ABCDEF123456", "zzzzzzzzzzzz", "..%2f..%2fx", "short"))
          and c.post("/api/runs/ABCDEF123456/remove", json={}, headers=OK).status_code == 400)
    check("removing a well-formed hash that does not exist -> 404", c.post("/api/runs/000000000000/remove", json={}, headers=OK).status_code == 404)

    # ---- soft remove is reversible and does not touch the store -----------------------------------------------------
    r = c.post(url, json={"reason": "test"}, headers=OK)
    check("remove succeeds", r.status_code == 200 and r.json()["status"] == "removed", r.text[:120])
    check("remove leaves runs.parquet byte-identical", sha(rm.RUNS_PATH) == parquet_hash)
    check("remove leaves the trade log in place", (rm.TRADES_DIR / f"{H}.parquet").exists())
    check("the run is gone from its page (404)", c.get(f"/api/runs/{H}").status_code == 404)
    hh = c.get("/api/health").json()
    check("counts drop by exactly one and report the removal", hh["n_runs"] == N - 1 and hh["n_removed"] == 1, str(hh))
    check("the study count drops by one", n_study() == base_study - 1)
    g = c.get(f"{study}/{row.tag}/{row.start}..{row.end}/grid").json()
    check("no grid cell points at the removed run", H not in {h for cell in g.get("cells", []) for h in cell["hashes"]})
    check("the removed run does not appear in run lists", H not in {x["hash"] for x in c.get("/api/runs?limit=1000").json()})
    check("removing again is idempotent", c.post(url, json={}, headers=OK).json()["status"] == "already_removed")
    lst = c.get("/api/removed").json()
    check("it is listed as removed, still in the store, with its trade log",
          len(lst) == 1 and lst[0]["hash"] == H and lst[0]["in_store"] and lst[0]["has_trade_log"] and lst[0]["reason"] == "test")

    r = c.post(f"/api/removed/{H}/restore", headers=OK)
    check("restore succeeds", r.status_code == 200)
    check("restore brings every count back exactly", c.get("/api/health").json()["n_runs"] == N and n_study() == base_study)
    check("the restored run's page works again", c.get(f"/api/runs/{H}").status_code == 200)
    check("restore never touched runs.parquet", sha(rm.RUNS_PATH) == parquet_hash)
    check("restoring something not removed -> 404", c.post(f"/api/removed/{H}/restore", headers=OK).status_code == 404)

    # ---- permanent delete: only after removal, only with the typed hash --------------------------------------------
    check("delete of a run that is not removed is refused (409)", c.post(f"/api/removed/{H}/purge", json={"confirm": H}, headers=OK).status_code == 409)
    c.post(url, json={}, headers=OK)
    check("delete with the wrong confirmation is refused (400)", c.post(f"/api/removed/{H}/purge", json={"confirm": "nope"}, headers=OK).status_code == 400)
    check("delete with a missing body is refused (422)", c.post(f"/api/removed/{H}/purge", headers=OK).status_code == 422)
    check("refused deletes changed nothing", sha(rm.RUNS_PATH) == parquet_hash and (rm.TRADES_DIR / f"{H}.parquet").exists())

    r = c.post(f"/api/removed/{H}/purge", json={"confirm": H}, headers=OK)
    check("delete succeeds", r.status_code == 200 and r.json()["status"] == "deleted", r.text[:150])
    after = pd.read_parquet(rm.RUNS_PATH)
    check("exactly that run's row is gone and every other row is identical",
          len(after) == N - 1 and H not in set(after.config_hash)
          and after.reset_index(drop=True).equals(raw[raw.config_hash != H].reset_index(drop=True)))
    check("column types survive the rewrite", dict(after.dtypes) == dict(raw.dtypes))
    baks = sorted(tmp.glob("runs.parquet.bak-*"))
    check("a backup of the previous store was written and still contains the run",
          len(baks) == 1 and H in set(pd.read_parquet(baks[0]).config_hash) and len(pd.read_parquet(baks[0])) == N)
    check("the trade log was MOVED to trades/_deleted, not unlinked",
          not (rm.TRADES_DIR / f"{H}.parquet").exists() and (rm.TRADES_DIR / "_deleted" / f"{H}.parquet").exists())
    check("the run is permanently gone from the API", c.get(f"/api/runs/{H}").status_code == 404 and c.get("/api/removed").json() == [])
    log = [json.loads(l) for l in (tmp / "removed_runs.log.jsonl").read_text().splitlines()]
    check("every action was audited, in order", [e["action"] for e in log] == ["remove", "restore", "remove", "purge"], str([e["action"] for e in log]))
    check("the audit records where the backup and trade log went", log[-1]["backup"] == baks[0].name and log[-1]["trade_log_moved_to"] == f"_deleted/{H}.parquet")
    check("a deleted run can no longer be removed or restored (404)",
          c.post(f"/api/runs/{H}/remove", json={}, headers=OK).status_code == 404 and c.post(f"/api/removed/{H}/restore", headers=OK).status_code == 404)

    # ---- refuse to write while the store is changing underneath us -------------------------------------------------
    c.post(f"/api/runs/{H2}/remove", json={}, headers=OK)
    n_bak = len(list(tmp.glob("runs.parquet.bak-*")))
    try:
        removal.purge(H2, H2, _between_read_and_write=lambda: os.utime(rm.RUNS_PATH, None))   # a notebook "writes"
        refused = False
    except rm.Conflict:
        refused = True
    check("delete is refused (Conflict) when runs.parquet changes mid-operation", refused)
    check("the refused delete removed nothing and made no backup",
          H2 in set(pd.read_parquet(rm.RUNS_PATH).config_hash) and len(list(tmp.glob("runs.parquet.bak-*"))) == n_bak
          and (rm.TRADES_DIR / f"{H2}.parquet").exists() and not list(tmp.glob("*.tmp")))
    check("...and the run is still removed (recoverable), not lost", any(t["hash"] == H2 for t in rm.read_tombstones()))

    # ---- a corrupt sidecar must not silently un-hide anything -------------------------------------------------------
    rm.REMOVED_PATH.write_text("{ this is not json", encoding="utf-8")
    rm.invalidate()
    r = c500.get("/api/strategies")
    check("a corrupt removed_runs.json is an error (500), not 'nothing is removed'", r.status_code == 500, str(r.status_code))
    rm.REMOVED_PATH.write_text(json.dumps([{"hash": H3, "removed_at": "x", "name": "n", "tag": "t", "window": "w", "strategy": "s", "reason": ""}]))
    rm.invalidate()
    check("a valid sidecar takes effect immediately (cache follows the file)", c.get("/api/health").json()["n_removed"] == 1)
finally:
    rm.RUNS_PATH, rm.TRADES_DIR, rm.REMOVED_PATH = saved
    rm.invalidate()
    shutil.rmtree(tmp, ignore_errors=True)

real_after = (sha(REAL / "runs.parquet"), (REAL / "removed_runs.json").exists())
check("the REAL results store was not touched by any of this", real_before == real_after, f"{real_before} vs {real_after}")
print(f"\n{len(fails)} failed" if fails else "\nall removal checks passed")
sys.exit(1 if fails else 0)
