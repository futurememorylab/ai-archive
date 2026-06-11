"""humanise(exc) produces actionable, non-empty error strings for the
common exception types that show up in user-facing surfaces (annotator
job errors, sync engine errors). Avoids the 'HTTPStatusError' bare-
class-name failure mode where str(exc) is empty."""

import httpx

from backend.app.services.errors import humanise


def test_humanise_handles_httpx_status_error_with_body():
    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(
        500, request=request, text='{"error": "internal", "code": "EFOO"}'
    )
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    msg = humanise(exc)
    assert "500" in msg
    assert "EFOO" in msg or "internal" in msg
    assert msg != "HTTPStatusError"


def test_humanise_handles_httpx_status_error_with_empty_body():
    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(503, request=request, text="")
    exc = httpx.HTTPStatusError("503", request=request, response=response)
    msg = humanise(exc)
    assert "503" in msg


def test_humanise_handles_connect_error():
    exc = httpx.ConnectError("Connection refused")
    msg = humanise(exc)
    # Output is "connect failed: Connection refused" — assert BOTH the
    # transport-phrase prefix AND that the exception text flows through.
    assert "refused" in msg.lower()
    assert "connect" in msg.lower()
    assert msg != "ConnectError"


def test_humanise_handles_timeout_error():
    exc = httpx.ConnectTimeout("read timed out after 30s")
    msg = humanise(exc)
    # ConnectTimeout is a TimeoutException (specific branch) — must not
    # fall through to the generic RequestError branch.
    assert "timeout" in msg.lower()
    assert "timed out" in msg.lower()


def test_humanise_handles_timeout_with_empty_message():
    # httpx.ReadTimeout often carries no message — str(exc) == "". The
    # prefetch/writeback stalls over the WireGuard tunnel surface exactly
    # this, and a bare "transport timeout: " (dangling colon, no info) is
    # what users were seeing. humanise must name the failure mode + type.
    exc = httpx.ReadTimeout("")
    assert str(exc) == ""
    msg = humanise(exc)
    assert "timeout" in msg.lower()
    assert "ReadTimeout" in msg
    assert not msg.rstrip().endswith(":")


def test_humanise_handles_arbitrary_exception_with_str():
    exc = RuntimeError("specific failure mode")
    msg = humanise(exc)
    assert "specific failure mode" in msg


def test_humanise_handles_arbitrary_exception_without_str():
    class _Mute(Exception):
        def __str__(self) -> str:
            return ""

    exc = _Mute()
    msg = humanise(exc)
    # Falls through to type name so the user is not left with "".
    assert "_Mute" in msg


def test_humanise_truncates_giant_bodies():
    request = httpx.Request("POST", "http://example/x")
    body = "x" * 5000
    response = httpx.Response(500, request=request, text=body)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    msg = humanise(exc)
    # Expected cap: _MAX_BODY_CHARS (400) + URL/prefix overhead (~80) ≈ 480.
    # The < 600 bound catches a doubling of _MAX_BODY_CHARS without false
    # positives from URL length variations.
    assert len(msg) < 600, f"got {len(msg)} chars; should be bounded"
