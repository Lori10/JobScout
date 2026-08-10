from datetime import datetime, timedelta, timezone

import pytest

from jobscout.config import FilterConfig, KeywordGroup, Profile, RankerConfig
from jobscout.models import ContractTypeGuess, EligibilityBucket, Job, RankingSource
from jobscout.ranker import guess_contract_type, score_job, trivial_rank_result


@pytest.fixture
def ranker_config() -> RankerConfig:
    return RankerConfig(
        keyword_groups={
            "llm_rag_genai": KeywordGroup(weight=3.0, phrases=["llm", "rag", "langchain", "genai"]),
            "ml_nlp_core": KeywordGroup(weight=2.0, phrases=["machine learning", "nlp", "pytorch"]),
            "python_generic": KeywordGroup(weight=1.0, phrases=["python", "docker", "sql"]),
        },
        title_match_bonus=15,
        positive_signal_bonus_cap=15,
        recency_bonus_max=10,
        near_miss_penalty=10,
    )


@pytest.fixture
def filter_config() -> FilterConfig:
    return FilterConfig(
        exclude_phrases=[],
        hybrid_onsite_phrases=[],
        remote_indicator_phrases=[],
        needs_review_phrases=[],
        positive_phrases=["worldwide", "b2b", "async", "freelance"],
        relevance_keywords=["ai", "llm", "python"],
    )


@pytest.fixture
def profile() -> Profile:
    return Profile(
        role_targets=["AI Engineer", "LLM Engineer", "Machine Learning Engineer"],
        core_skills=["RAG", "LangChain", "Python", "Docker", "PyTorch", "FastAPI"],
    )


def make_job(**overrides) -> Job:
    defaults = dict(
        title="Backend Engineer",
        company="Acme",
        description="",
        url="https://example.com/job",
        source="test",
        eligibility_bucket=EligibilityBucket.ELIGIBLE,
        is_relevant=True,
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_score_and_related_fields_within_bounds(ranker_config, filter_config, profile):
    job = make_job(title="LLM Engineer", description="LLM RAG LangChain GenAI Python Docker worldwide b2b async")
    result = score_job(job, ranker_config, filter_config, profile)
    assert 0 <= result.score <= 100
    assert 0 <= result.skill_match <= 100
    assert 0 <= result.eligibility_confidence <= 100


def test_llm_rag_keywords_score_higher_than_generic_python(ranker_config, filter_config, profile):
    job_a = make_job(title="Engineer", description="We use Python and Docker every day.")
    job_b = make_job(title="Engineer", description="We build LLM and RAG pipelines with LangChain.")
    result_a = score_job(job_a, ranker_config, filter_config, profile)
    result_b = score_job(job_b, ranker_config, filter_config, profile)
    assert result_b.score > result_a.score


def test_title_match_bonus_applied(ranker_config, filter_config, profile):
    job_with_title_match = make_job(title="LLM Engineer", description="Generic description with no keywords.")
    job_without = make_job(title="Backend Engineer", description="Generic description with no keywords.")
    result_with = score_job(job_with_title_match, ranker_config, filter_config, profile)
    result_without = score_job(job_without, ranker_config, filter_config, profile)
    assert result_with.score > result_without.score
    assert any("role target" in r for r in result_with.reasons)


def test_positive_signals_boost_score(ranker_config, filter_config, profile):
    job_with_signals = make_job(description="Fully remote, worldwide, b2b, async team.")
    job_without_signals = make_job(description="Fully remote team, nothing else mentioned.")
    result_with = score_job(job_with_signals, ranker_config, filter_config, profile)
    result_without = score_job(job_without_signals, ranker_config, filter_config, profile)
    assert result_with.score > result_without.score


def test_recency_bonus_favors_newer_posts(ranker_config, filter_config, profile):
    now = datetime.now(timezone.utc)
    recent_job = make_job(description="Python role.", posted_date=now)
    old_job = make_job(description="Python role.", posted_date=now - timedelta(days=60))
    result_recent = score_job(recent_job, ranker_config, filter_config, profile)
    result_old = score_job(old_job, ranker_config, filter_config, profile)
    assert result_recent.score > result_old.score


@pytest.mark.parametrize(
    "description,expected",
    [
        ("This is a B2B contract, invoice monthly.", ContractTypeGuess.B2B),
        ("Freelance position, 1099 contractor.", ContractTypeGuess.FREELANCE),
        ("Full-time employment, permanent position.", ContractTypeGuess.EMPLOYMENT),
        ("No mention of any contracting structure here.", ContractTypeGuess.UNCLEAR),
    ],
)
def test_contract_type_guess_branches(description, expected):
    from jobscout.filters import normalize_text

    assert guess_contract_type(normalize_text(description)) == expected


def test_b2b_wins_over_freelance_when_both_present():
    from jobscout.filters import normalize_text

    text = normalize_text("This is a B2B contract role, freelance-friendly.")
    assert guess_contract_type(text) == ContractTypeGuess.B2B


def test_bare_contractor_alone_guesses_freelance():
    # Real gap found via live audit: real postings say "Contractor", not
    # the longer "contract role"/"contract position" the old list required.
    from jobscout.filters import normalize_text

    text = normalize_text("Looking for a contractor to join the team, remote friendly.")
    assert guess_contract_type(text) == ContractTypeGuess.FREELANCE


def test_bare_full_time_alone_guesses_employment():
    # Real gap: postings almost always say bare "Full-time"/"Full Time",
    # not the redundant "full-time employment" the old list required.
    from jobscout.filters import normalize_text

    text = normalize_text("This is a Full-time role based remotely.")
    assert guess_contract_type(text) == ContractTypeGuess.EMPLOYMENT


def test_contractor_wins_over_full_time_when_both_present():
    from jobscout.filters import normalize_text

    text = normalize_text("Contractor position, full-time hours expected.")
    assert guess_contract_type(text) == ContractTypeGuess.FREELANCE


def test_ranking_source_is_always_heuristic(ranker_config, filter_config, profile):
    job = make_job(description="Python role.")
    result = score_job(job, ranker_config, filter_config, profile)
    assert result.ranking_source == RankingSource.HEURISTIC


def test_needs_review_bucket_lowers_eligibility_confidence(ranker_config, filter_config, profile):
    job = make_job(description="Python role.", eligibility_bucket=EligibilityBucket.NEEDS_REVIEW)
    result = score_job(job, ranker_config, filter_config, profile)
    assert result.eligibility_confidence < 100
    assert any("needs_review" in flag for flag in result.red_flags)


def test_needs_review_job_never_floors_to_zero_like_trivial_rank_result(ranker_config, filter_config, profile):
    # Real case: a weak-signal needs_review job (PostHog/EMEA) had its
    # already-low raw score pushed negative by the near-miss penalty and
    # clamped to 0 - visually identical to an excluded/irrelevant job that
    # was never ranked at all. A relevant needs_review job must stay
    # distinguishable from that.
    job = make_job(
        title="Generic Role",
        description="No matching keywords here at all.",
        eligibility_bucket=EligibilityBucket.NEEDS_REVIEW,
    )
    result = score_job(job, ranker_config, filter_config, profile)
    assert result.score >= 1


def test_trivial_rank_result_for_excluded_job():
    job = make_job(eligibility_bucket=EligibilityBucket.EXCLUDED, eligibility_reason="excluded: matched ['us only']")
    result = trivial_rank_result(job)
    assert result.score == 0
    assert result.eligibility_confidence == 0
    assert result.ranking_source == RankingSource.HEURISTIC


def test_trivial_rank_result_for_irrelevant_job():
    job = make_job(is_relevant=False)
    result = trivial_rank_result(job)
    assert result.score == 0
    assert "irrelevant" in result.reasons[0]
