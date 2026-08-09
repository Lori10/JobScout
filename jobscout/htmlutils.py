"""Shared HTML-to-plain-text helpers used by fetchers, filters, ranker, and report.

All three Phase 1 sources return HTML- or entity-laden text (RemoteOK/Remotive
descriptions are raw HTML; HN comment bodies are HTML-entity-encoded, e.g.
``&#x2F;`` for ``/``). Every downstream consumer wants plain text, so we strip
once at fetch time and never carry raw HTML on the Job object.
"""

from __future__ import annotations

import html
import re

_BLOCK_TAG_RE = re.compile(r"</?(p|div|br|li|tr|h[1-6])\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")


def strip_html(text: str | None) -> str:
    """Unescape HTML entities and strip tags, collapsing whitespace.

    Block-level tags are converted to newlines first so paragraphs/list
    items don't run together into one unreadable line.
    """
    if not text:
        return ""
    unescaped = html.unescape(text)
    with_breaks = _BLOCK_TAG_RE.sub("\n", unescaped)
    no_tags = _TAG_RE.sub(" ", with_breaks)
    collapsed = _WS_RE.sub(" ", no_tags)
    lines = [line.strip() for line in collapsed.splitlines()]
    non_empty = [line for line in lines if line]
    return "\n".join(non_empty).strip()
