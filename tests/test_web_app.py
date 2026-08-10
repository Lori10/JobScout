from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from jobscout.db import init_db, upsert_job
from jobscout.models import EligibilityBucket, Job, JobStatus
from jobscout.web.app import create_app


def make_job(**overrides) -> Job:
    defaults = dict(
        title="LLM Engineer",
        company="Acme",
        description="Build LLM pipelines.",
        url="https://example.com/jobs/1",
        source="remoteok",
        dedup_key="https://example.com/jobs/1",
        score=80,
        eligibility_bucket=EligibilityBucket.ELIGIBLE,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "jobscout.db")
    conn = init_db(db_path)
    upsert_job(conn, make_job())
    upsert_job(
        conn,
        make_job(
            title="Data Scientist",
            dedup_key="https://example.com/jobs/2",
            url="https://example.com/jobs/2",
            score=40,
        ),
    )
    conn.close()

    app = create_app(db_path=db_path, frontend_dist=str(tmp_path / "no-dist"))
    return TestClient(app)


def test_list_jobs_returns_seeded_jobs_with_plain_string_enums(client):
    response = client.get("/api/jobs")
    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 2
    titles = {j["title"] for j in jobs}
    assert titles == {"LLM Engineer", "Data Scientist"}
    assert jobs[0]["eligibility_bucket"] == "eligible"
    assert jobs[0]["status"] == "new"


def test_update_job_status_updates_and_returns_job(client):
    response = client.post(
        "/api/jobs/status",
        json={"dedup_key": "https://example.com/jobs/1", "status": "interested"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "interested"

    jobs = client.get("/api/jobs").json()
    updated = next(j for j in jobs if j["dedup_key"] == "https://example.com/jobs/1")
    assert updated["status"] == "interested"


def test_update_job_status_404s_on_unknown_dedup_key(client):
    response = client.post(
        "/api/jobs/status",
        json={"dedup_key": "https://example.com/does-not-exist", "status": "applied"},
    )
    assert response.status_code == 404


def test_update_job_status_422s_on_invalid_status(client):
    response = client.post(
        "/api/jobs/status",
        json={"dedup_key": "https://example.com/jobs/1", "status": "bogus"},
    )
    assert response.status_code == 422


def test_fetch_now_calls_pipeline_run_once_and_returns_refreshed_jobs(client):
    with patch("jobscout.web.routes.pipeline.run") as mock_run:
        response = client.post("/api/fetch")

    assert response.status_code == 200
    mock_run.assert_called_once()
    assert len(response.json()) == 2


def test_fetch_now_returns_500_when_pipeline_run_fails(client):
    with patch("jobscout.web.routes.pipeline.run", side_effect=RuntimeError("boom")):
        response = client.post("/api/fetch")

    assert response.status_code == 500
