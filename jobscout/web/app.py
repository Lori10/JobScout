"""FastAPI app factory for the Phase 2 dashboard.

Serves the JSON API under /api and the built React frontend (frontend/dist)
as static files at /. The module-level `app` reads JOBSCOUT_* env vars so
`uvicorn jobscout.web.app:app` works standalone in dev, and
`python -m jobscout serve` (which sets those env vars from argparse first)
goes through the exact same construction path.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from jobscout.web.routes import router

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DIST_DIR = _REPO_ROOT / "frontend" / "dist"


def create_app(
    *,
    config_path: str = "config.yaml",
    profile_path: str = "profile.yaml",
    db_path: str = "data/jobscout.db",
    report_path: str = "report.html",
    frontend_dist: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="JobScout Dashboard")
    app.state.config_path = config_path
    app.state.profile_path = profile_path
    app.state.db_path = db_path
    app.state.report_path = report_path

    # Registered before the static mount so /api/* is matched first.
    app.include_router(router)

    dist_dir = Path(frontend_dist) if frontend_dist is not None else _DEFAULT_DIST_DIR
    if dist_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="frontend")
    else:
        logger.warning(
            "frontend build not found at %s -- run `npm install && npm run build` "
            "in frontend/. The API is still served under /api.",
            dist_dir,
        )

    return app


app = create_app(
    config_path=os.environ.get("JOBSCOUT_CONFIG_PATH", "config.yaml"),
    profile_path=os.environ.get("JOBSCOUT_PROFILE_PATH", "profile.yaml"),
    db_path=os.environ.get("JOBSCOUT_DB_PATH", "data/jobscout.db"),
    report_path=os.environ.get("JOBSCOUT_REPORT_PATH", "report.html"),
)
