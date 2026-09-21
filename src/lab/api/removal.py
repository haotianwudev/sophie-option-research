"""Removing runs from the viewer -- reversible by default, with a separate deliberate delete.

This is the only part of the API that changes anything. Two levels, kept apart on purpose:

REMOVE  (default, reversible)
    Records the run in `results/removed_runs.json` and the read model hides it everywhere (studies, grids,
    counts, run pages). `runs.parquet` and the trade log are NOT touched, so this cannot lose data and cannot
    race a notebook that is writing runs. Restore deletes the record and the run is back exactly as it was.

DELETE PERMANENTLY  (only for a run that is already removed, and only with the hash typed as confirmation)
    Drops the run's rows from `runs.parquet` and moves its trade log to `trades/_deleted/` (moved, not unlinked).
    Before writing, `runs.parquet` is copied to a timestamped `.bak-` file, and the write is refused (409) if the
    file changed while we were working, because the store is not process-safe (`save_run` only locks threads) and
    a notebook may be writing to it. Every action is appended to `removed_runs.log.jsonl`. It does not touch the
    Sophie Postgres: a run that was published stays published there.

All paths are read from `readmodel` at call time (not copied at import), so tests can point them at a scratch copy.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

from . import readmodel as rm
from .readmodel import BadRequest, Conflict, NotFound, clean

HASH_RE = re.compile(r"^[0-9a-f]{12}$")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _check(h: str) -> None:
    if not HASH_RE.match(h or ""):
        raise BadRequest("a run hash is 12 lowercase hex characters")


def _write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    os.replace(tmp, path)      # atomic: a reader never sees a half-written file


def _audit(entry: dict[str, Any]) -> None:
    log = rm.REMOVED_PATH.with_name("removed_runs.log.jsonl")
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now(), **entry}) + "\n")


# ---------------------------------------------------------------------------

def remove(h: str, reason: str = "") -> dict[str, Any]:
    _check(h)
    with _lock:
        tombs = rm.read_tombstones()
        if any(t["hash"] == h for t in tombs):
            return {"status": "already_removed", "hash": h}
        s = rm.load()
        m = s.runs[s.runs["config_hash"] == h]
        if m.empty:
            raise NotFound(f"no run {h}")
        row = m.sort_values("run_at").iloc[-1]
        tomb = {"hash": h, "removed_at": _now(), "reason": (reason or "")[:200], "name": row["name"],
                "strategy": row["strategy"], "tag": ", ".join(sorted(m["tag"].unique())), "window": row["window"]}
        _write_json(rm.REMOVED_PATH, tombs + [tomb])
        _audit({"action": "remove", **tomb})
        rm.invalidate()
        return {"status": "removed", **tomb}


def restore(h: str) -> dict[str, Any]:
    _check(h)
    with _lock:
        tombs = rm.read_tombstones()
        if not any(t["hash"] == h for t in tombs):
            raise NotFound(f"run {h} is not in the removed list")
        _write_json(rm.REMOVED_PATH, [t for t in tombs if t["hash"] != h])
        _audit({"action": "restore", "hash": h})
        rm.invalidate()
        return {"status": "restored", "hash": h}


def listing() -> list[dict[str, Any]]:
    """Removed runs, newest removal first, with their headline numbers read from the (untouched) store."""
    tombs = rm.read_tombstones()
    if not tombs:
        return []
    df = pd.read_parquet(rm.RUNS_PATH) if rm.RUNS_PATH.exists() else pd.DataFrame(columns=["config_hash"])
    out = []
    for t in sorted(tombs, key=lambda x: x["removed_at"], reverse=True):
        m = df[df["config_hash"] == t["hash"]]
        row = m.iloc[-1] if len(m) else None
        out.append({**t, "in_store": row is not None,
                    "total_trades": None if row is None else row.get("total_trades"),
                    "sharpe_ratio": None if row is None else row.get("sharpe_ratio"),
                    "run_at": None if row is None else row.get("run_at"),
                    "has_trade_log": (rm.TRADES_DIR / f"{t['hash']}.parquet").exists()})
    return clean(out)


def purge(h: str, confirm: str, _between_read_and_write: Optional[Callable[[], None]] = None) -> dict[str, Any]:
    """Permanently delete a run that has already been removed. `_between_read_and_write` exists for tests."""
    _check(h)
    if confirm != h:
        raise BadRequest("confirmation must be the run's hash, typed exactly")
    with _lock:
        tombs = rm.read_tombstones()
        if not any(t["hash"] == h for t in tombs):
            raise Conflict("remove the run first; only a removed run can be deleted permanently")
        p = rm.RUNS_PATH
        m0 = p.stat().st_mtime_ns
        df = pd.read_parquet(p)
        hit = df[df["config_hash"] == h]
        if hit.empty:
            raise NotFound(f"run {h} is not in the results store")
        keep = df[df["config_hash"] != h]
        if _between_read_and_write:
            _between_read_and_write()
        if p.stat().st_mtime_ns != m0:
            raise Conflict("results/runs.parquet changed while deleting (a notebook may be writing). "
                           "Nothing was deleted; try again once it has finished.")
        backup = p.with_name(f"{p.name}.bak-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(p, backup)
        tmp = p.with_name(p.name + ".tmp")
        keep.to_parquet(tmp, index=False)
        if p.stat().st_mtime_ns != m0:            # re-check right before the swap
            tmp.unlink(missing_ok=True)
            raise Conflict("results/runs.parquet changed while deleting. Nothing was deleted; try again.")
        os.replace(tmp, p)

        moved = None
        tp = rm.TRADES_DIR / f"{h}.parquet"
        if tp.exists():
            dest = rm.TRADES_DIR / "_deleted"
            dest.mkdir(exist_ok=True)
            shutil.move(str(tp), str(dest / tp.name))
            moved = f"_deleted/{tp.name}"

        _write_json(rm.REMOVED_PATH, [t for t in tombs if t["hash"] != h])
        _audit({"action": "purge", "hash": h, "rows_removed": int(len(hit)), "backup": backup.name,
                "trade_log_moved_to": moved, "name": str(hit.iloc[-1]["name"])})
        rm.invalidate()
        return {"status": "deleted", "hash": h, "rows_removed": int(len(hit)), "backup": backup.name,
                "trade_log_moved_to": moved}
