import pytest

from jobscout.config import FilterConfig
from jobscout.filters import (
    apply_eligibility_filter,
    apply_keyword_relevance_filter,
    find_phrase_matches,
    normalize_text,
)
from jobscout.models import EligibilityBucket, Job


@pytest.fixture
def filter_config() -> FilterConfig:
    return FilterConfig(
        exclude_phrases=[
            "us only",
            "us citizens only",
            "us-based",
            "green card",
            "w2 only",
            "w-2 only",
            "authorized to work in the us",
            "visa sponsorship required",
            "must relocate",
            "security clearance",
            "must work pst hours",
            "must work pacific hours",
        ],
        hybrid_onsite_phrases=["hybrid", "onsite", "on-site"],
        remote_indicator_phrases=["remote", "fully remote", "work from home"],
        needs_review_phrases=["eu only", "europe only", "emea", "european timezones", "eu work authorization"],
        positive_phrases=["worldwide", "anywhere", "b2b", "freelance"],
        relevance_keywords=["ai", "llm", "machine learning", "rag", "nlp", "langchain", "pytorch", "data scientist"],
    )


def make_job(title: str = "AI Engineer", description: str = "", location: str | None = None) -> Job:
    return Job(
        title=title,
        company="Acme",
        description=description,
        url="https://example.com/job",
        source="test",
        location_text=location,
    )


def test_us_citizens_only_excluded(filter_config):
    job = make_job(description="Must be a US citizen. US citizens only please.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.EXCLUDED
    assert "us citizens only" in result.eligibility_reason


def test_eu_only_needs_review_not_excluded(filter_config):
    job = make_job(description="This role is EU only, fully remote otherwise.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.NEEDS_REVIEW


def test_worldwide_eligible(filter_config):
    job = make_job(description="We hire worldwide, fully remote, async team.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_est_overlap_4h_eligible_not_excluded(filter_config):
    job = make_job(description="Looking for some overlap with EST, about 4 hours a day is fine.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_pst_hours_required_excluded(filter_config):
    job = make_job(description="You must work PST hours to coordinate with the team.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.EXCLUDED


def test_hybrid_without_remote_mention_excluded(filter_config):
    job = make_job(description="This is a hybrid role, 3 days per week in our downtown office.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.EXCLUDED


def test_hybrid_with_remote_mentioned_not_excluded(filter_config):
    job = make_job(description="Hybrid or fully remote, your choice - we support both.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_german_post_not_excluded_for_being_german(filter_config):
    job = make_job(
        title="KI Ingenieur",
        description=(
            "Wir suchen einen erfahrenen KI Ingenieur fuer unser Team. "
            "Die Stelle ist komplett remote, Freelancer willkommen, B2B moeglich. "
            "Gute Deutschkenntnisse von Vorteil."
        ),
    )
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_visa_sponsorship_required_excluded(filter_config):
    job = make_job(description="Visa sponsorship required for this position.")
    assert apply_eligibility_filter(job, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_green_card_excluded(filter_config):
    job = make_job(description="Candidates must hold a green card.")
    assert apply_eligibility_filter(job, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_security_clearance_excluded(filter_config):
    job = make_job(description="Active security clearance is required.")
    assert apply_eligibility_filter(job, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_case_insensitivity(filter_config):
    job = make_job(description="US CITIZENS ONLY need apply.")
    assert apply_eligibility_filter(job, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_punctuation_tolerance_us_dot_style(filter_config):
    job = make_job(description="U.S. Citizens Only, no exceptions.")
    assert apply_eligibility_filter(job, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_hyphen_and_space_variants_are_equivalent(filter_config):
    job_hyphen = make_job(description="This role is US-based only.")
    job_space = make_job(description="This role is US based only.")
    assert apply_eligibility_filter(job_hyphen, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED
    assert apply_eligibility_filter(job_space, filter_config).eligibility_bucket == EligibilityBucket.EXCLUDED


def test_exclude_wins_over_needs_review_when_both_present(filter_config):
    job = make_job(description="EU only, but also US citizens only requirement applies.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.EXCLUDED


def test_short_keyword_does_not_match_inside_unrelated_word():
    text_norm = normalize_text(
        "This role involves maintaining legacy html templates and being certain about deadlines."
    )
    hits = find_phrase_matches(text_norm, ["ai", "ml"])
    assert hits == []


def test_keyword_relevance_positive_match(filter_config):
    job = make_job(title="RAG Engineer", description="Build LLM-powered RAG pipelines with LangChain.")
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is True


def test_keyword_relevance_no_match_is_irrelevant(filter_config):
    job = make_job(title="Warehouse Associate", description="Pack boxes and operate a forklift all day.")
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is False


def test_keyword_relevance_independent_of_eligibility(filter_config):
    # An excluded job can still be marked relevant - the two filters are orthogonal.
    job = make_job(
        title="LLM Engineer",
        description="US citizens only. Build LLM pipelines with RAG and LangChain.",
    )
    elig = apply_eligibility_filter(job, filter_config)
    relevance = apply_keyword_relevance_filter(elig, filter_config)
    assert relevance.eligibility_bucket == EligibilityBucket.EXCLUDED
    assert relevance.is_relevant is True
