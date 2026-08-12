import logging
from pathlib import Path

import pytest
import yaml

import jobscout.pipeline as pipeline_module
from jobscout.ai_ranker import AIRankingError, _AIRankingSchema
from jobscout.db import init_db, upsert_job
from jobscout.llm.base import LLMResult, TransientProviderError
from jobscout.models import Job, RankingSource


def _write_yaml(path: Path, data: dict) -> str:
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(path)


def _base_config(**overrides) -> dict:
    data = {
        "ranking_mode": "heuristic",
        "min_score_threshold": 0,
        "filters": {"relevance_keywords": ["python"]},
        "ai": {"provider": "gemini", "model": "gemini-2.5-flash", "max_retries": 0},
    }
    data.update(overrides)
    return data


def _fake_fetcher_cls(jobs: list[Job]):
    class _Fetcher:
        def fetch(self):
            return list(jobs)

    return _Fetcher


def make_job(**overrides) -> Job:
    defaults = dict(
        title="Python Engineer",
        company="Acme",
        description="We need a strong python engineer.",
        url="https://example.com/jobs/1",
        source="fake",
        dedup_key="https://example.com/jobs/1",
    )
    defaults.update(overrides)
    return Job(**defaults)


class _FakeProvider:
    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    def complete_structured(self, *, system, user_message, schema, max_tokens):
        self.call_count += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(parsed=item, input_tokens=10, output_tokens=5)


def _ai_schema(**overrides) -> _AIRankingSchema:
    defaults = dict(
        is_job_posting=True,
        score=70,
        skill_match=50,
        contract_type_guess="employment",
        seniority_fit="match",
        reasons=["fits"],
        red_flags=[],
    )
    defaults.update(overrides)
    return _AIRankingSchema(**defaults)


@pytest.fixture
def paths(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_yaml(config_path, _base_config())
    profile_path = _write_yaml(tmp_path / "profile.yaml", {"role_targets": [], "core_skills": []})
    db_path = str(tmp_path / "jobscout.db")
    report_path = str(tmp_path / "report.html")
    return config_path, profile_path, db_path, report_path


def test_heuristic_mode_unaffected(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)

    conn = init_db(db_path)
    jobs = conn.execute("SELECT * FROM jobs").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["ranking_source"] == "heuristic"
    assert jobs[0]["score"] is not None


def test_ai_mode_without_api_key_falls_back_to_heuristic(paths, monkeypatch, caplog):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(ranking_mode="ai"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    with caplog.at_level(logging.WARNING):
        pipeline_module.run(str(config_path), profile_path, db_path, report_path)

    assert any("GEMINI_API_KEY" in r.message for r in caplog.records)
    conn = init_db(db_path)
    jobs = conn.execute("SELECT * FROM jobs").fetchall()
    assert jobs[0]["ranking_source"] == "heuristic"


def test_ai_mode_with_key_uses_provider_and_caches_on_second_run(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(ranking_mode="ai"))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    provider = _FakeProvider([_ai_schema(score=88)])
    monkeypatch.setattr(pipeline_module, "build_provider", lambda name, api_key, model: provider)

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    conn = init_db(db_path)
    jobs = conn.execute("SELECT * FROM jobs").fetchall()
    assert jobs[0]["ranking_source"] == "ai"
    assert jobs[0]["score"] == 88
    assert provider.call_count == 1

    # Second run: job is already AI-ranked, so the cache should be used
    # instead of calling the provider again.
    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    assert provider.call_count == 1


def test_ai_mode_falls_back_to_heuristic_for_a_job_whose_ai_call_fails(paths, monkeypatch, caplog):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(ranking_mode="ai"))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    provider = _FakeProvider([TransientProviderError("boom")])
    monkeypatch.setattr(pipeline_module, "build_provider", lambda name, api_key, model: provider)

    with caplog.at_level(logging.WARNING):
        pipeline_module.run(str(config_path), profile_path, db_path, report_path)

    conn = init_db(db_path)
    jobs = conn.execute("SELECT * FROM jobs").fetchall()
    assert jobs[0]["ranking_source"] == "heuristic"
    assert jobs[0]["score"] is not None
    assert any("falling back to heuristic" in r.message for r in caplog.records)


def test_rerank_job_success_bypasses_cache(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    conn = init_db(db_path)
    upsert_job(conn, make_job(ranking_source=RankingSource.AI, score=10))

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = _FakeProvider([_ai_schema(score=99)])
    monkeypatch.setattr(pipeline_module, "build_provider", lambda name, api_key, model: provider)

    job = pipeline_module.rerank_job("https://example.com/jobs/1", str(config_path), profile_path, db_path)
    assert job.score == 99
    assert provider.call_count == 1


def test_rerank_job_raises_for_unknown_dedup_key(paths):
    config_path, profile_path, db_path, report_path = paths
    init_db(db_path)
    with pytest.raises(ValueError):
        pipeline_module.rerank_job("https://example.com/does-not-exist", str(config_path), profile_path, db_path)


def test_rerank_job_raises_on_ai_failure_instead_of_falling_back(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    conn = init_db(db_path)
    upsert_job(conn, make_job())

    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    provider = _FakeProvider([TransientProviderError("boom")])
    monkeypatch.setattr(pipeline_module, "build_provider", lambda name, api_key, model: provider)

    with pytest.raises(AIRankingError):
        pipeline_module.rerank_job("https://example.com/jobs/1", str(config_path), profile_path, db_path)


def test_rerank_job_raises_when_no_api_key_configured(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    conn = init_db(db_path)
    upsert_job(conn, make_job())
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(AIRankingError):
        pipeline_module.rerank_job("https://example.com/jobs/1", str(config_path), profile_path, db_path)
