from jobscout.fetchers.common import (
    contract_type_from_label,
    extract_apply_url,
    extract_href_urls,
    extract_url,
    format_location_restrictions,
    parse_rfc822_datetime,
    parse_unix_timestamp,
)
from jobscout.models import ContractTypeGuess


def test_extract_url_returns_first_url_naively():
    text = "See our blog: https://acme.com/blog then apply: https://acme.com/careers"
    assert extract_url(text) == "https://acme.com/blog"


def test_extract_apply_url_returns_none_for_no_urls():
    assert extract_apply_url("no links here") is None


def test_extract_apply_url_single_url_unchanged_behavior():
    text = "Email us or apply here: https://acme.com/some-random-path"
    assert extract_apply_url(text) == "https://acme.com/some-random-path"


def test_extract_apply_url_prefers_careers_path_over_earlier_blog_link():
    # Real case: Langfuse's HN post links its own "why we joined ClickHouse"
    # blog post, ClickHouse's blog post, and its handbook before the actual
    # apply link — all three earlier links used to win under a naive
    # first-URL-in-text approach.
    text = (
        "Why we joined: https://langfuse.com/blog/joining-clickhouse\n"
        "ClickHouse's take: https://clickhouse.com/blog/clickhouse-acquires-langfuse\n"
        "Our handbook: https://langfuse.com/handbook\n"
        "Check out our open roles and apply to the one you think fits you best: "
        "https://langfuse.com/careers"
    )
    assert extract_apply_url(text) == "https://langfuse.com/careers"


def test_extract_apply_url_prefers_ats_domain_over_earlier_generic_link():
    text = "Read about us at https://acme.com/about. Apply via https://boards.greenhouse.io/acme/jobs/123"
    assert extract_apply_url(text) == "https://boards.greenhouse.io/acme/jobs/123"


def test_extract_apply_url_prefers_apply_context_phrase_over_earlier_link():
    text = "Our story: https://acme.com/story. Please apply here: https://acme.com/xyz123"
    assert extract_apply_url(text) == "https://acme.com/xyz123"


def test_extract_apply_url_falls_back_to_first_url_when_nothing_scores_higher():
    text = "Contact https://acme.com/one or https://acme.com/two for details."
    assert extract_apply_url(text) == "https://acme.com/one"


def test_extract_apply_url_strips_trailing_punctuation():
    text = "Apply here: https://acme.com/careers."
    assert extract_apply_url(text) == "https://acme.com/careers"


# ---- extract_href_urls / truncated-display-text recovery ----


def test_extract_href_urls_unescapes_html_entities():
    raw_html = '<a href="https:&#x2F;&#x2F;acme.com&#x2F;careers" rel="nofollow">apply</a>'
    assert extract_href_urls(raw_html) == ["https://acme.com/careers"]


def test_extract_href_urls_returns_empty_list_for_no_links():
    assert extract_href_urls("just plain text") == []
    assert extract_href_urls(None) == []


def test_extract_apply_url_recovers_full_url_when_display_text_is_truncated():
    # Real case: HN's rendering truncates a long URL's *displayed* text
    # with "..." while the href attribute keeps the complete URL. Once
    # strip_html() discards the tag, the plain text only has the
    # truncated version - extract_apply_url must prefer the full href.
    plain_text = (
        "Apply here: https://jobs.ashbyhq.com/TonicAI/048a114d-fb5f-46ef-b0ff-b62... "
        "but also shoot me an email."
    )
    href_urls = ["https://jobs.ashbyhq.com/TonicAI/048a114d-fb5f-46ef-b0ff-b62365ff5fc2"]
    assert extract_apply_url(plain_text, href_urls) == "https://jobs.ashbyhq.com/TonicAI/048a114d-fb5f-46ef-b0ff-b62365ff5fc2"


def test_extract_apply_url_ignores_href_urls_not_matching_any_candidate():
    plain_text = "Apply here: https://acme.com/careers"
    href_urls = ["https://unrelated.com/other-page"]
    assert extract_apply_url(plain_text, href_urls) == "https://acme.com/careers"


def test_extract_apply_url_works_without_href_urls_argument():
    # Backward-compatible default - no truncation recovery attempted.
    assert extract_apply_url("Apply here: https://acme.com/careers") == "https://acme.com/careers"


# --- Phase 4 groundwork: date parsing beyond ISO8601 ---


def test_parse_rfc822_datetime_parses_rss_pubdate():
    dt = parse_rfc822_datetime("Wed, 22 Jul 2026 07:02:04 +0000")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 7, 22, 7)
    assert dt.tzinfo is not None


def test_parse_rfc822_datetime_returns_none_for_iso_input():
    # The two parsers are not interchangeable; an RSS source passing an ISO
    # string here should fail loudly-as-None rather than silently succeed.
    assert parse_rfc822_datetime("2026-07-22T07:02:04+00:00") is None


def test_parse_rfc822_datetime_returns_none_for_garbage_and_empty():
    assert parse_rfc822_datetime("not a date") is None
    assert parse_rfc822_datetime("") is None
    assert parse_rfc822_datetime(None) is None


def test_parse_unix_timestamp_seconds_and_milliseconds():
    seconds = parse_unix_timestamp(1786516800)
    assert seconds is not None and seconds.year == 2026
    # Lever's createdAt is milliseconds; the same value read as seconds
    # would land ~1000x further in the future, hence the explicit unit.
    millis = parse_unix_timestamp(1779223091267, milliseconds=True)
    assert millis is not None and millis.year == 2026


def test_parse_unix_timestamp_is_utc_aware():
    dt = parse_unix_timestamp(1786516800)
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_unix_timestamp_rejects_non_numeric_and_bool():
    assert parse_unix_timestamp(None) is None
    assert parse_unix_timestamp("") is None
    assert parse_unix_timestamp("not a number") is None
    # bool is an int subclass — True would otherwise become 1970-01-01.
    assert parse_unix_timestamp(True) is None


def test_parse_unix_timestamp_accepts_numeric_string():
    assert parse_unix_timestamp("1786516800") is not None


# --- Phase 4 groundwork: structured employment-type mapping ---


def test_contract_type_from_label_freelance_variants():
    for label in ("Contractor", "Contract", "Freelance", "freiberuflich", "Fixed term"):
        assert contract_type_from_label(label) == ContractTypeGuess.FREELANCE, label


def test_contract_type_from_label_employment_variants():
    # Arbeitnow alone emits all three casings/spellings of "full time".
    for label in ("Full-Time", "Full time", "Permanent", "Festanstellung", "Werkstudent"):
        assert contract_type_from_label(label) == ContractTypeGuess.EMPLOYMENT, label


def test_contract_type_from_label_unclear_for_empty_and_unknown():
    for label in ("", None, "Experienced", "Student college", 42):
        assert contract_type_from_label(label) == ContractTypeGuess.UNCLEAR, label


def test_contract_type_from_label_list_takes_first_non_unclear():
    # Real Arbeitnow shape: job_types is an array, often with noise first.
    assert contract_type_from_label(["Experienced", "Contract"]) == ContractTypeGuess.FREELANCE
    assert contract_type_from_label(["Full-Time"]) == ContractTypeGuess.EMPLOYMENT
    assert contract_type_from_label([]) == ContractTypeGuess.UNCLEAR


def test_contract_type_from_label_prefers_freelance_over_employment():
    # "Contract, full-time hours" is a contract engagement first — same
    # priority ranker.guess_contract_type applies to prose.
    assert contract_type_from_label("Contract (full-time hours)") == ContractTypeGuess.FREELANCE


# --- Phase 4 groundwork: structured hiring-location allow-lists ---


def test_format_location_restrictions_marks_them_as_restrictions():
    # A bare country name reads as a mention; Stage 1 needs it to read as
    # the restriction the source field actually asserts.
    assert format_location_restrictions(["United States"]) == "United States only"
    assert format_location_restrictions("USA") == "USA only"
    assert format_location_restrictions(["United States", "Canada"]) == "United States, Canada only"


def test_format_location_restrictions_lands_in_existing_config_vocabulary():
    # "USA only" is already an exclude_phrase; "Europe only" already a
    # needs_review_phrase. That reuse is the point of the "only" suffix.
    assert format_location_restrictions("USA") == "USA only"
    assert format_location_restrictions("Europe") == "Europe only"


def test_format_location_restrictions_leaves_unrestricted_values_alone():
    # "Anywhere only" would invert the meaning.
    for value in ("Anywhere in the World", "Worldwide", "Remote", "Global"):
        assert format_location_restrictions(value) == value, value


def test_format_location_restrictions_does_not_double_up_only():
    assert format_location_restrictions("USA Only") == "USA Only"


def test_format_location_restrictions_empty_is_none():
    assert format_location_restrictions([]) is None
    assert format_location_restrictions(None) is None
    assert format_location_restrictions("") is None
    assert format_location_restrictions(["", "  "]) is None
