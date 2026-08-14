import logging
from pathlib import Path

import pytest
import yaml

import jobscout.pipeline as pipeline_module
from jobscout.ai_ranker import AIRankingError, _AIRankingSchema
from jobscout.db import get_known_boards, init_db, upsert_job, upsert_known_board
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
        # Off by default so existing tests stay hermetic — both features make
        # real outbound HTTP calls (a board's own ATS API, a GitHub raw
        # README fetch) when enabled. Tests that specifically exercise them
        # turn them on explicitly and mock the network boundary.
        "board_registry": {"enabled": False},
        "github_lists": {"enabled": False},
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
    # instead of calling the provider again. This also doubles as the
    # regression test for hoisting conn = init_db(db_path) above the fetch
    # loop (it used to open only after dedupe) — if that reorder broke the
    # AI-ranking cache read, provider.call_count would be 2 here and the
    # fake provider's exhausted response queue would raise instead.
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


def _fake_fetcher_with_discovered_boards(jobs, discovered):
    class _Fetcher:
        def __init__(self):
            self.discovered_boards = discovered

        def fetch(self):
            return list(jobs)

    return _Fetcher


def test_run_polls_known_boards_and_includes_results_in_dedupe(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(board_registry={"enabled": True}))
    conn = init_db(db_path)
    upsert_known_board(conn, platform="ashby", board_slug="starbridge", company="Starbridge", discovered_via="hn_whoishiring", discovered_url=None)
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {})

    registry_job = make_job(
        title="Registry Role", url="https://jobs.ashbyhq.com/starbridge/1", dedup_key="https://jobs.ashbyhq.com/starbridge/1", source="ats_board_registry"
    )
    monkeypatch.setattr(pipeline_module, "poll_known_boards", lambda conn, max_workers: [registry_job])

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    conn = init_db(db_path)
    jobs = conn.execute("SELECT * FROM jobs").fetchall()
    assert len(jobs) == 1
    assert jobs[0]["title"] == "Registry Role"
    assert jobs[0]["source"] == "ats_board_registry"


def test_run_records_discovered_boards_from_hn_fetcher(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config())
    fetcher_cls = _fake_fetcher_with_discovered_boards(
        [make_job()], [("ashby", "starbridge", "Starbridge", "https://jobs.ashbyhq.com/starbridge")]
    )
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"hn_whoishiring": fetcher_cls})

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)

    conn = init_db(db_path)
    boards = get_known_boards(conn)
    assert len(boards) == 1
    assert boards[0].platform == "ashby"
    assert boards[0].board_slug == "starbridge"
    assert boards[0].discovered_via == "hn_whoishiring"


def test_run_respects_board_registry_disabled_toggle(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(board_registry={"enabled": False}))
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    calls = []
    monkeypatch.setattr(pipeline_module, "poll_known_boards", lambda conn, max_workers: calls.append(1) or [])

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    assert calls == []


def test_run_respects_github_lists_disabled_toggle(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(github_lists={"enabled": False}))
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    calls = []
    monkeypatch.setattr(pipeline_module, "seed_from_github_lists", lambda conn, config: calls.append(1) or [])

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    assert calls == []


def test_run_calls_github_lists_seeding_when_enabled(paths, monkeypatch):
    config_path, profile_path, db_path, report_path = paths
    _write_yaml(config_path, _base_config(github_lists={"enabled": True}))
    monkeypatch.setattr(pipeline_module, "FETCHER_REGISTRY", {"fake": _fake_fetcher_cls([make_job()])})

    calls = []
    monkeypatch.setattr(pipeline_module, "seed_from_github_lists", lambda conn, config: calls.append(1) or [])

    pipeline_module.run(str(config_path), profile_path, db_path, report_path)
    assert calls == [1]
