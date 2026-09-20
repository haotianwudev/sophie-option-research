"""Local-only JSON API over the research results store.

LOCAL ONLY. This binds to loopback and allowlists exactly the two localhost origins the
viewer UI runs on. There is deliberately no hosted path: it serves every run, trade log
and memo in the store with no auth. It is READ-ONLY -- nothing here writes to results/,
publishes to Postgres or launches a backtest (each of those would need its own gate). Same
posture as sophie-pipeline/sophie_agent/server/server.py.

Route shape follows the data: a study is (strategy, tag, window), because tags mix windows
and a `baseline` (tag, window) holds two strategies.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import availability as av
from . import readmodel as rm
from .memo import EngineUnavailable, study_memo

UI_PORT = 3010


@asynccontextmanager
async def lifespan(_: FastAPI):
    # The chain-archive scan reads ~4k files (10-20 s). Do it in the background at startup so the first
    # request to /api/data/availability is not the one that pays for it.
    threading.Thread(target=lambda: _warm(), daemon=True).start()
    yield


def _warm() -> None:
    try:
        av.availability()
    except Exception:  # a missing archive is reported by the endpoint itself; never block startup
        pass


app = FastAPI(title="sophie option research viewer", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://localhost:{UI_PORT}", f"http://127.0.0.1:{UI_PORT}"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(rm.NotFound)
async def _not_found(_, exc: rm.NotFound):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(rm.BadRequest)
async def _bad_request(_, exc: rm.BadRequest):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
def health():
    return rm.health()


@app.get("/api/data/availability")
def data_availability():
    """Coverage, content and freshness of the raw SPX option-chain archive (see lab/api/availability.py)."""
    return av.availability()


@app.get("/api/strategies")
def strategies():
    return rm.strategies()


@app.get("/api/strategies/{strategy}/studies")
def studies(strategy: str):
    return rm.studies(strategy)


@app.get("/api/strategies/{strategy}/studies/{tag}/{window}/params")
def study_params(strategy: str, tag: str, window: str):
    return rm.study_params(strategy, tag, window)


@app.get("/api/strategies/{strategy}/studies/{tag}/{window}/grid")
def study_grid(strategy: str, tag: str, window: str,
               x: Optional[str] = None, y: Optional[str] = None,
               metric: str = rm.DEFAULT_METRIC,
               fix: list[str] = Query(default=[], description="repeatable key:level")):
    pins = {}
    for item in fix:
        key, sep, level = item.rpartition(":")
        if not sep or not key:
            raise rm.BadRequest(f"fix must look like key:level, got {item!r}")
        pins[key] = level
    return rm.grid(strategy, tag, window, x=x, y=y, metric=metric, fix=pins)


@app.get("/api/studies/{tag}/memo")
def memo(tag: str, hypothesis: str = ""):
    try:
        return study_memo(tag, hypothesis)
    except EngineUnavailable as e:
        raise HTTPException(503, f"backtest engine unavailable, memo disabled: {e}")


# `tag` is None unless the parameter is present: an empty `tag=` is a real filter on the
# empty tag, not "everything" (which is what lab.experiments.load_runs("") would do).
@app.get("/api/runs")
def runs(strategy: Optional[str] = None, tag: Optional[str] = None,
         sort: str = rm.DEFAULT_METRIC, limit: int = Query(200, ge=1, le=1000),
         asc: bool = False):
    return rm.runs(strategy=strategy, tag=tag, sort=sort, limit=limit, descending=not asc)


@app.get("/api/runs/{h}")
def run(h: str):
    return rm.run(h)


@app.get("/api/runs/{h}/trades")
def trades(h: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    return rm.trades(h, offset, limit)


@app.get("/api/runs/{h}/equity")
def equity(h: str):
    return rm.equity(h)


@app.get("/api/runs/{h}/regimes")
def regimes(h: str, feature: str = "vix_rank", bins: int = Query(4, ge=2, le=10)):
    try:
        return rm.regimes(h, feature, bins)
    except rm.FeaturesUnavailable as e:
        raise HTTPException(503, f"regime breakdown unavailable: {e}")
