import os
import sqlite3
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest

from jobscout.db import (
    SCHEMA,
    SCHEMA_POSTGRES,
    get_job_by_dedup_key,
    get_jobs,
    get_known_boards,
    get_last_scan,
    init_db,
    record_board_poll,
    record_scan,
    set_board_enabled,
    set_status,
    upsert_job,
    upsert_known_board,
)
from jobscout.models import EligibilityBucket, Job, JobStatus, RankingSource, SeniorityFit


@pytest.fixture(params=["sqlite", "postgres"])
def conn(request):
    if request.param == "postgres":
        dsn = os.environ.get("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("TEST_DATABASE_URL not set; skipping Postgres backend tests")
        connection = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)
        with connection.cursor() as cur:
            cur.execute(SCHEMA_POSTGRES)
            # Postgres is a shared, persistent server (unlike sqlite ":memory:"),
            # and CREATE TABLE IF NOT EXISTS leaves old rows in place across
            # test runs/tests — truncate for a clean slate before every test.
            cur.execute("TRUNCATE jobs, known_boards, github_list_scans RESTART IDENTITY CASCADE")
        connection.commit()
        yield connection
        connection.close()
    else:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(SCHEMA)
        connection.commit()
        yield connection
        connection.close()


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


def test_upsert_known_board_insert_and_get(conn):
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company="Starbridge",
        discovered_via="hn_whoishiring",
        discovered_url="https://jobs.ashbyhq.com/starbridge",
    )
    boards = get_known_boards(conn)
    assert len(boards) == 1
    assert boards[0].platform == "ashby"
    assert boards[0].board_slug == "starbridge"
    assert boards[0].company == "Starbridge"
    assert boards[0].enabled is True


def test_upsert_known_board_first_seen_at_not_clobbered(conn):
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company=None,
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    first_seen = get_known_boards(conn)[0].first_seen_at

    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company="Starbridge Inc",
        discovered_via="github:established-remote",
        discovered_url=None,
    )
    board = get_known_boards(conn)[0]
    assert board.first_seen_at == first_seen
    assert board.company == "Starbridge Inc"
    assert board.discovered_via == "github:established-remote"


def test_upsert_known_board_enabled_not_clobbered_by_rediscovery(conn):
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company=None,
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    set_board_enabled(conn, platform="ashby", board_slug="starbridge", enabled=False)

    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company=None,
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    board = get_known_boards(conn, enabled_only=False)[0]
    assert board.enabled is False


def test_upsert_known_board_unique_constraint(conn):
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company=None,
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company=None,
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    assert len(get_known_boards(conn, enabled_only=False)) == 1


def test_get_known_boards_enabled_only_filter(conn):
    upsert_known_board(
        conn, platform="ashby", board_slug="a", company=None, discovered_via="hn_whoishiring", discovered_url=None
    )
    upsert_known_board(
        conn, platform="ashby", board_slug="b", company=None, discovered_via="hn_whoishiring", discovered_url=None
    )
    set_board_enabled(conn, platform="ashby", board_slug="b", enabled=False)

    assert {b.board_slug for b in get_known_boards(conn, enabled_only=True)} == {"a"}
    assert {b.board_slug for b in get_known_boards(conn, enabled_only=False)} == {"a", "b"}


def test_record_board_poll_updates_only_poll_fields(conn):
    upsert_known_board(
        conn,
        platform="ashby",
        board_slug="starbridge",
        company="Starbridge",
        discovered_via="hn_whoishiring",
        discovered_url=None,
    )
    record_board_poll(conn, platform="ashby", board_slug="starbridge", result_count=5)

    board = get_known_boards(conn)[0]
    assert board.last_poll_result_count == 5
    assert board.last_polled_at is not None
    assert board.company == "Starbridge"
    assert board.discovered_via == "hn_whoishiring"


def test_github_list_scan_get_and_record_roundtrip(conn):
    assert get_last_scan(conn, "established-remote") is None

    when = datetime.now(timezone.utc) - timedelta(days=1)
    record_scan(conn, "established-remote", when)
    stored = get_last_scan(conn, "established-remote")
    assert stored is not None
    assert abs((stored - when).total_seconds()) < 1

    later = datetime.now(timezone.utc)
    record_scan(conn, "established-remote", later)
    stored_again = get_last_scan(conn, "established-remote")
    assert abs((stored_again - later).total_seconds()) < 1
