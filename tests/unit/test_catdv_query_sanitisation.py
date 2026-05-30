"""list_clips's user-search input must not be able to escape its embedding
in the CatDV query expression. Today's q.replace('(', '').replace(')', '')
only handles parens; quotes, the keywords 'and'/'or', backslashes are not
handled.

We use an allowlist: only alphanumerics, space, hyphen, underscore, and
dot pass. Anything else is stripped. The CatDV server then sees a search
fragment that cannot escape the (clip.name)contains(...) wrapper."""

import pytest

from backend.app.services.catdv_client import _sanitise_query


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain text", "plain text"),
        ("plain (with parens)", "plain with parens"),
        ("quote\"injection", "quoteinjection"),
        ("backslash\\here", "backslashhere"),
        ("and-or-keyword", "and-or-keyword"),
        (") or (1)eq(1", " or 1eq1"),
        ("hyphen-and_underscore.dot 123", "hyphen-and_underscore.dot 123"),
        ("", ""),
    ],
)
def test_sanitise_query(raw: str, expected: str):
    assert _sanitise_query(raw) == expected
