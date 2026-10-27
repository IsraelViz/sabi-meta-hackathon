"""Run Sabi locally with a week of demo trading, for trying the owner app.

    python scripts/dev_server.py        # then open http://127.0.0.1:8000/app/
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("STORE", "memory")
os.environ.setdefault("DEMO_HISTORY", "1")

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
