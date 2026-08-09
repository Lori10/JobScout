import sqlite3

import pytest

from jobscout.db import get_jobs, init_db, set_status, upsert_job
from jobscout.models import EligibilityBucket, Job, JobStatus


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    from jobscout.db import SCHEMA

    connection.executescript(SCHEMA)
    connection.commit()
    return connection


def make_job(**overrides) -> Job:
    defaults = dict(
        title="LLM Engineer",
        company="Acme",
        description="Build LLM pipelines.",
        url="https://example.com/jobs/1",
        source="remoteok",
        dedup_key="https://example.com/jobs/1",
        score=80,
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_upsert_and_get(conn):
    job = make_job()
    upsert_job(conn, job)
    jobs = get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].title == "LLM Engineer"
    assert jobs[0].score == 80
    assert jobs[0].status == JobStatus.NEW


def test_reupsert_preserves_status(conn):
    job = make_job()
    upsert_job(conn, job)
    set_status(conn, job.dedup_key, JobStatus.INTERESTED)

    updated_job = make_job(score=95, eligibility_bucket=EligibilityBucket.NEEDS_REVIEW)
    upsert_job(conn, updated_job)

    jobs = get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.INTERESTED
    assert jobs[0].score == 95
    assert jobs[0].eligibility_bucket == EligibilityBucket.NEEDS_REVIEW


def test_get_jobs_rejects_unsafe_order_by(conn):
    upsert_job(conn, make_job())
    with pytest.raises(ValueError):
        get_jobs(conn, order_by="score; DROP TABLE jobs;")


def test_get_jobs_order_by_score_desc(conn):
    upsert_job(conn, make_job(dedup_key="a", url="https://example.com/a", score=50))
    upsert_job(conn, make_job(dedup_key="b", url="https://example.com/b", score=90))
    jobs = get_jobs(conn, order_by="score DESC")
    assert [j.score for j in jobs] == [90, 50]
