import sqlite3

import pytest

from jobscout.db import get_job_by_dedup_key, get_jobs, init_db, set_status, upsert_job
from jobscout.models import EligibilityBucket, Job, JobStatus, RankingSource, SeniorityFit


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


def test_seniority_fit_round_trips_through_upsert(conn):
    job = make_job(ranking_source=RankingSource.AI, seniority_fit=SeniorityFit.OVER_QUALIFIED)
    upsert_job(conn, job)
    jobs = get_jobs(conn)
    assert jobs[0].seniority_fit == SeniorityFit.OVER_QUALIFIED


def test_seniority_fit_defaults_to_none(conn):
    upsert_job(conn, make_job())
    jobs = get_jobs(conn)
    assert jobs[0].seniority_fit is None


def test_get_job_by_dedup_key_found(conn):
    upsert_job(conn, make_job())
    job = get_job_by_dedup_key(conn, "https://example.com/jobs/1")
    assert job is not None
    assert job.title == "LLM Engineer"


def test_get_job_by_dedup_key_not_found(conn):
    assert get_job_by_dedup_key(conn, "https://example.com/does-not-exist") is None


def test_init_db_migrates_seniority_fit_column_onto_pre_phase3_db(tmp_path):
    # Simulates a DB created before seniority_fit existed: build the table
    # with the schema string minus that column, then confirm init_db() adds
    # it via the PRAGMA-guarded ALTER TABLE migration, without losing data.
    path = tmp_path / "old.db"
    old_schema = """
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dedup_key TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        source_id TEXT,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        description TEXT NOT NULL,
        url TEXT NOT NULL,
        posted_date TEXT,
        salary_text TEXT,
        tags TEXT,
        location_text TEXT,
        application_channel TEXT NOT NULL DEFAULT 'unknown',
        eligibility_bucket TEXT NOT NULL DEFAULT 'eligible',
        eligibility_reason TEXT,
        is_relevant INTEGER NOT NULL DEFAULT 1,
        score INTEGER,
        skill_match INTEGER,
        eligibility_confidence INTEGER,
        contract_type_guess TEXT NOT NULL DEFAULT 'unclear',
        reasons TEXT,
        red_flags TEXT,
        ranking_source TEXT NOT NULL DEFAULT 'heuristic',
        status TEXT NOT NULL DEFAULT 'new',
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        research_brief_path TEXT,
        outreach_draft_path TEXT
    );
    """
    setup_conn = sqlite3.connect(path)
    setup_conn.executescript(old_schema)
    setup_conn.execute(
        "INSERT INTO jobs (dedup_key, source, title, company, description, url, first_seen_at, last_seen_at) "
        "VALUES ('k1', 'remoteok', 'Old Job', 'Acme', 'desc', 'https://example.com/1', '2024-01-01', '2024-01-01')"
    )
    setup_conn.commit()
    setup_conn.close()

    conn = init_db(path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    assert "seniority_fit" in columns

    jobs = get_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0].title == "Old Job"
    assert jobs[0].seniority_fit is None
