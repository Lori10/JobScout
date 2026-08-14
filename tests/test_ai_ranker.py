import pytest

import jobscout.ai_ranker as ai_ranker_module
from jobscout.ai_ranker import AIRanker, AIRankingError, _AIRankingSchema
from jobscout.config import AIConfig, Profile
from jobscout.llm.base import FatalProviderError, LLMResult, TransientProviderError
from jobscout.models import (
    ContractTypeGuess,
    EligibilityBucket,
    Job,
    RankingSource,
    SeniorityFit,
)


class _FakeProvider:
    """Injected in place of a real LLMProvider. `responses` is a list of
    either an _AIRankingSchema instance (success) or an exception instance
    (raised) consumed in order, one per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    def complete_structured(self, *, system, user_message, schema, max_tokens):
        self.call_count += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return LLMResult(parsed=item, input_tokens=100, output_tokens=50)


def make_schema(**overrides) -> _AIRankingSchema:
    defaults = dict(
        is_job_posting=True,
        score=75,
        skill_match=60,
        contract_type_guess="employment",
        seniority_fit="match",
        reasons=["good fit"],
        red_flags=[],
    )
    defaults.update(overrides)
    return _AIRankingSchema(**defaults)


def make_job(**overrides) -> Job:
    defaults = dict(
        title="LLM Engineer",
        company="Acme",
        description="Build LLM pipelines.",
        url="https://example.com/jobs/1",
        source="remoteok",
        dedup_key="https://example.com/jobs/1",
        eligibility_bucket=EligibilityBucket.ELIGIBLE,
        is_relevant=True,
    )
    defaults.update(overrides)
    return Job(**defaults)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    # Retry backoff sleeps in real code; tests don't need to wait for it.
    monkeypatch.setattr(ai_ranker_module.time, "sleep", lambda _seconds: None)


@pytest.fixture
def ai_config() -> AIConfig:
    return AIConfig(provider="gemini", model="gemini-2.5-flash", max_description_chars=4000, max_retries=2)


@pytest.fixture
def profile() -> Profile:
    return Profile(role_targets=["AI Engineer"], core_skills=["Python"])


def test_successful_score_maps_onto_rank_result(ai_config, profile):
    provider = _FakeProvider([make_schema(score=80, skill_match=55, seniority_fit="over_qualified")])
    ranker = AIRanker(provider, ai_config, profile)
    result = ranker.score_job(make_job())

    assert result.score == 80
    assert result.skill_match == 55
    assert result.ranking_source == RankingSource.AI
    assert result.seniority_fit == SeniorityFit.OVER_QUALIFIED
    assert result.contract_type_guess == ContractTypeGuess.EMPLOYMENT
    # eligibility_confidence is always computed locally from the bucket, not
    # something the LLM output could ever influence (the schema has no such
    # field at all).
    assert result.eligibility_confidence == 100


def test_needs_review_job_gets_lower_eligibility_confidence_from_local_map(ai_config, profile):
    provider = _FakeProvider([make_schema()])
    ranker = AIRanker(provider, ai_config, profile)
    job = make_job(eligibility_bucket=EligibilityBucket.NEEDS_REVIEW)
    result = ranker.score_job(job)
    assert result.eligibility_confidence == 60


def test_non_job_posting_forces_score_zero_and_adds_red_flag(ai_config, profile):
    provider = _FakeProvider([make_schema(is_job_posting=False, score=90)])
    ranker = AIRanker(provider, ai_config, profile)
    result = ranker.score_job(make_job())
    assert result.score == 0
    assert result.ranking_source == RankingSource.AI
    assert any("not a genuine job posting" in flag for flag in result.red_flags)


def test_fatal_error_disables_ranker_for_rest_of_run(ai_config, profile):
    provider = _FakeProvider([FatalProviderError("bad key")])
    ranker = AIRanker(provider, ai_config, profile)

    with pytest.raises(AIRankingError):
        ranker.score_job(make_job())
    assert provider.call_count == 1

    # Second call must not hit the provider again.
    with pytest.raises(AIRankingError):
        ranker.score_job(make_job(dedup_key="https://example.com/jobs/2"))
    assert provider.call_count == 1


def test_transient_error_retried_then_succeeds(ai_config, profile):
    provider = _FakeProvider([TransientProviderError("rate limited"), make_schema(score=42)])
    ranker = AIRanker(provider, ai_config, profile)
    result = ranker.score_job(make_job())
    assert result.score == 42
    assert provider.call_count == 2


def test_transient_error_exhausts_retries_then_raises(ai_config, profile):
    # max_retries=2 means 3 total attempts.
    provider = _FakeProvider([TransientProviderError("e1"), TransientProviderError("e2"), TransientProviderError("e3")])
    ranker = AIRanker(provider, ai_config, profile)
    with pytest.raises(AIRankingError):
        ranker.score_job(make_job())
    assert provider.call_count == 3


def test_summary_line_before_any_calls(ai_config, profile):
    ranker = AIRanker(_FakeProvider([]), ai_config, profile)
    assert "no calls" in ranker.summary_line()


def test_summary_line_after_calls_reports_tokens_and_cost(ai_config, profile):
    provider = _FakeProvider([make_schema(), make_schema()])
    ranker = AIRanker(provider, ai_config, profile)
    ranker.score_job(make_job())
    ranker.score_job(make_job(dedup_key="https://example.com/jobs/2"))
    line = ranker.summary_line()
    assert "2 call(s)" in line
    assert "200 input" in line
    assert "100 output" in line


def test_fetcher_set_contract_type_overrides_ai_guess(ai_config, profile):
    # Same precedence as ranker.resolve_contract_type: a source that states
    # the engagement type as structured data beats any reading of the prose,
    # so the contract column means the same thing in both ranking modes.
    provider = _FakeProvider([make_schema(contract_type_guess="employment")])
    ranker = AIRanker(provider, ai_config, profile)
    job = make_job(contract_type_guess=ContractTypeGuess.FREELANCE)
    assert ranker.score_job(job).contract_type_guess == ContractTypeGuess.FREELANCE


def test_ai_contract_type_used_when_fetcher_left_it_unclear(ai_config, profile):
    provider = _FakeProvider([make_schema(contract_type_guess="b2b")])
    ranker = AIRanker(provider, ai_config, profile)
    assert ranker.score_job(make_job()).contract_type_guess == ContractTypeGuess.B2B
