from jobscout.fetchers.common import extract_apply_url, extract_href_urls, extract_url


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
