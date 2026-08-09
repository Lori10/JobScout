from jobscout.htmlutils import strip_html


def test_strip_html_removes_tags():
    raw = "<p>Hello <b>world</b></p><p>Second paragraph</p>"
    result = strip_html(raw)
    assert "<" not in result
    assert "Hello world" in result
    assert "Second paragraph" in result


def test_strip_html_hex_entity_slash():
    # The exact entity observed in real HN "Who is hiring" comment bodies.
    raw = "https:&#x2F;&#x2F;example.com&#x2F;jobs"
    result = strip_html(raw)
    assert result == "https://example.com/jobs"


def test_strip_html_empty_and_none():
    assert strip_html("") == ""
    assert strip_html(None) == ""


def test_strip_html_collapses_whitespace_and_blank_lines():
    raw = "<p>Line one</p>\n\n<p>   </p><p>Line two</p>"
    result = strip_html(raw)
    assert result == "Line one\nLine two"


def test_strip_html_named_entities():
    raw = "AT&amp;T &mdash; great &amp; simple"
    result = strip_html(raw)
    assert "&amp;" not in result
    assert "AT&T" in result
