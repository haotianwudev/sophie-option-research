"""Study memo endpoint -- the one part of the API that needs the backtest engine.

lab.explain.build_memo pulls in lab.backtest, which imports optopsy at module level.
That import is deferred to call time and isolated here, so if the engine is missing or
broken only this endpoint fails (503) and everything else keeps serving.
"""

from __future__ import annotations

from typing import Any

from .readmodel import NotFound, clean, load


class EngineUnavailable(RuntimeError):
    """lab.explain could not be imported (optopsy missing or broken)."""


def study_memo(tag: str, hypothesis: str = "") -> dict[str, Any]:
    if tag not in set(load().runs["tag"]):
        raise NotFound(f"no runs with tag {tag!r}")
    try:
        from lab.explain import HEADLINE_METRICS, build_memo
    except Exception as e:
        raise EngineUnavailable(f"{type(e).__name__}: {e}") from e
    memo = build_memo(tag, hypothesis=hypothesis)
    memo["headline_metrics"] = list(HEADLINE_METRICS)
    return clean(memo)
