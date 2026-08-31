"""Phase 2 dashboard API routes: list jobs, update status, trigger a fetch.

Purely a read/write layer over db.py + pipeline.py — no new fetching,
filtering, or ranking logic lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request

from jobscout import pipeline
from jobscout.db import DBConnection, get_jobs, init_db, set_status
from jobscout.web.schemas import JobOut, RerankIn, StatusUpdateIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def get_db(request: Request) -> Iterator[DBConnection]:
    conn = init_db(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(conn: DBConnection = Depends(get_db)) -> list[JobOut]:
    return [JobOut.model_validate(job) for job in get_jobs(conn)]


@router.post("/jobs/status", response_model=JobOut)
def update_job_status(payload: StatusUpdateIn, conn: DBConnection = Depends(get_db)) -> JobOut:
    set_status(conn, payload.dedup_key, payload.status)
    for job in get_jobs(conn):
        if job.dedup_key == payload.dedup_key:
            return JobOut.model_validate(job)
    raise HTTPException(404, f"no job with dedup_key {payload.dedup_key!r}")


@router.post("/jobs/rerank", response_model=JobOut)
def rerank_job(payload: RerankIn, request: Request) -> JobOut:
    # A re-rank is an explicit request for an AI result, so failures are
    # surfaced (not silently downgraded to the heuristic score) — unlike
    # pipeline.run()'s bulk fallback behavior.
    try:
        job = pipeline.rerank_job(
            payload.dedup_key,
            config_path=request.app.state.config_path,
            profile_path=request.app.state.profile_path,
            db_path=request.app.state.db_path,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from None
    except Exception:
        logger.exception("pipeline.rerank_job() failed during /api/jobs/rerank")
        raise HTTPException(500, "re-rank failed; check server logs") from None
    return JobOut.model_validate(job)


@router.post("/fetch", response_model=list[JobOut])
def fetch_now(request: Request) -> list[JobOut]:
    try:
        pipeline.run(
            config_path=request.app.state.config_path,
            profile_path=request.app.state.profile_path,
            db_path=request.app.state.db_path,
            report_path=request.app.state.report_path,
        )
    except Exception:
        logger.exception("pipeline.run() failed during /api/fetch")
        raise HTTPException(500, "fetch failed; check server logs") from None

    conn = init_db(request.app.state.db_path)
    try:
        return [JobOut.model_validate(job) for job in get_jobs(conn)]
    finally:
        conn.close()
