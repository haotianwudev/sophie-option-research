"""Start the local research-viewer API on 127.0.0.1:8010.

    PYTHONPATH=src ./.venv/Scripts/python.exe scripts/serve_api.py [--port 8010] [--reload]

Loopback only, never 0.0.0.0 -- see lab/api/app.py. Runs from the repo root with no install.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("lab.api.app:app", host="127.0.0.1", port=a.port, reload=a.reload,
                app_dir=str(Path(__file__).resolve().parents[1] / "src"))
