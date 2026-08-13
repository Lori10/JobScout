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
            "not sponsoring visas",
            "not sponsoring visa",
            "must relocate",
            "security clearance",
            "must work pst hours",
            "must work pacific hours",
        ],
        hybrid_onsite_phrases=["hybrid", "onsite", "on-site", "in-office"],
        remote_indicator_phrases=["remote", "fully remote", "work from home"],
        needs_review_phrases=["eu only", "europe only", "emea", "european timezones", "eu work authorization"],
        positive_phrases=["worldwide", "anywhere", "b2b", "freelance"],
        relevance_keywords=["llm", "machine learning", "rag", "nlp", "langchain", "pytorch", "data scientist"],
        relevance_keywords_weak=["ai", "ml"],
        non_role_title_phrases=[
            "sales",
            "marketing",
            "graphic designer",
            "recruiter",
            "customer support",
            "head of growth",
            "ceo",
        ],
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


def test_negated_in_office_phrase_not_excluded(filter_config):
    # Real case: "This role does NOT have an in-office requirement" was
    # excluded by a plain substring match on "in-office", ignoring the
    # negation right before it.
    job = make_job(
        description=(
            "We may consider candidates outside Arizona. "
            "This role does not have an in-office requirement but would need to come in occasionally."
        )
    )
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.ELIGIBLE


def test_not_sponsoring_visas_phrasing_excluded(filter_config):
    # Real case found alongside the negation bug above: this exact
    # phrasing ("not sponsoring visas" rather than "no visa sponsorship")
    # was missing from the exclude list entirely.
    job = make_job(description="Great team, fully remote. We are not sponsoring visas at this time.")
    result = apply_eligibility_filter(job, filter_config)
    assert result.eligibility_bucket == EligibilityBucket.EXCLUDED
    assert "sponsoring visas" in result.eligibility_reason


def test_negation_does_not_suppress_a_non_negated_occurrence_elsewhere():
    # A phrase mentioned twice, negated once and not the other time, must
    # still count - negation only suppresses that specific occurrence.
    text = normalize_text("This is not onsite work, it's fully remote. We do have an onsite office in Berlin too.")
    hits = find_phrase_matches(text, ["onsite"])
    assert hits == ["onsite"]


def test_negation_word_itself_does_not_block_phrases_that_start_with_it():
    # "no visa sponsorship" legitimately starts with a negation word - the
    # negation check looks at words BEFORE the match, not the match itself.
    text = normalize_text("Please note: no visa sponsorship is available for this role.")
    hits = find_phrase_matches(text, ["no visa sponsorship"])
    assert hits == ["no visa sponsorship"]


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


def test_non_engineering_title_vetoes_relevance_despite_ai_keyword_in_description(filter_config):
    # Real case: company's product is "AI", but the ROLE is a commission-based
    # sales/affiliate gig - the bare "ai" keyword match must not override that.
    job = make_job(
        title="Hiring Sales Agent",
        description="Our product is a privacy-first AI browser copilot. Earn $5 per referral.",
    )
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is False


def test_non_engineering_title_vetoes_relevance_despite_marketplace_boilerplate(filter_config):
    # Real case: a design role on a marketplace whose generic template text
    # mentions engineers/AI projects elsewhere on the platform.
    job = make_job(
        title="Senior Graphic Designer",
        description="Join our marketplace of hand-picked startups building AI and machine learning products.",
    )
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is False


def test_head_of_growth_and_ceo_roles_vetoed(filter_config):
    job = make_job(
        title="Head of Growth and CEO roles",
        description="Venture studio hiring commercial leaders to own the business side of AI-powered SaaS.",
    )
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is False


def test_engineering_title_not_affected_by_non_role_phrases_elsewhere():
    config = FilterConfig(
        relevance_keywords=["llm", "rag"],
        non_role_title_phrases=["sales", "marketing"],
    )
    job = make_job(
        title="LLM Engineer",
        description="Work closely with our sales and marketing teams to build RAG-powered LLM tools.",
    )
    result = apply_keyword_relevance_filter(job, config)
    assert result.is_relevant is True


def test_bare_ai_mention_in_body_only_is_not_relevant(filter_config):
    # Real case: "please don't send me 5 paragraphs of AI text" made an
    # unrelated Account Executive/PM posting look relevant. A bare "ai"
    # hit with no other signal, and not in the title, must not pass.
    job = make_job(
        title="Account Executive, Defense and Aerospace",
        description="Email in profile. Please do not send 5 paragraphs of AI text, just a resume.",
    )
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is False


def test_bare_ai_in_title_is_relevant_even_without_other_signals(filter_config):
    job = make_job(title="AI Engineer", description="Join our small team building great products.")
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is True


def test_bare_ai_in_body_combined_with_strong_signal_is_relevant(filter_config):
    job = make_job(
        title="Backend Engineer",
        description="We use AI extensively, especially LLM-based RAG pipelines.",
    )
    result = apply_keyword_relevance_filter(job, filter_config)
    assert result.is_relevant is True


def test_ai_hiring_disclosure_boilerplate_does_not_trigger_relevance():
    # Real case: a retail "Loss Prevention Specialist" posting with zero
    # actual AI/ML content passed the relevance filter purely because of a
    # legally-mandated hiring-process disclosure sentence (NYC Local Law
    # 144-style: "Company uses Artificial Intelligence (AI) technology to
    # assist with the screening and assessment of applicants"). That's
    # about the employer's hiring process, not the job's substance.
    config = FilterConfig(relevance_keywords=["artificial intelligence"])
    job = make_job(
        title="Loss Prevention Specialist",
        description=(
            "Maintain aisle cleanliness and assist customers in the store. "
            "In our commitment to a fair hiring experience, The Home Depot Canada uses "
            "Artificial Intelligence (AI) technology to assist with the screening and "
            "assessment of applicants for this position."
        ),
    )
    result = apply_keyword_relevance_filter(job, config)
    assert result.is_relevant is False


def test_genuine_ai_mention_in_body_still_counts_when_not_disclosure_boilerplate():
    # The strip must be narrowly targeted at hiring-process disclosure
    # sentences, not any sentence mentioning AI near "applicants".
    config = FilterConfig(relevance_keywords=["artificial intelligence"])
    job = make_job(
        title="Backend Engineer",
        description="You will build the Artificial Intelligence platform that powers our product.",
    )
    result = apply_keyword_relevance_filter(job, config)
    assert result.is_relevant is True
