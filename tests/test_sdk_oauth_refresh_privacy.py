import asyncio
import logging

import httpx
import pytest
from test_sdk_oauth_refresh import MODES, Tracking, exchange, reply

from ezviz_unified import DiagnosticRecorder

MARKER_URL = "https://separate.example.invalid/other-visible"
SECRETS = ("SYN_BAD_VERSION", "SYN_PRIVATE_REASON")


def _handler(request):
    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    with httpx.Client(transport=transport) as separate:
        separate.get(MARKER_URL)
    return httpx.Response(
        200,
        json=reply(),
        extensions={
            "http_version": b"SYN_BAD_VERSION",
            "reason_phrase": b"SYN_PRIVATE_REASON",
        },
    )


@pytest.mark.parametrize("mode", MODES)
def test_refresh_response_extensions_stay_private(mode, caplog):
    tracking = Tracking(_handler)
    with caplog.at_level(logging.INFO, logger="httpx"):
        exchange(mode, tracking)
    assert MARKER_URL in caplog.text
    for secret in SECRETS:
        assert secret not in caplog.text
    assert tracking.closed
    assert len(tracking.requests) == 1


def test_refresh_cancelled_error_reports_ordered_outcomes():
    def boom(request):
        raise asyncio.CancelledError()

    tracking = Tracking(boom)
    recorder = DiagnosticRecorder()
    with pytest.raises(asyncio.CancelledError):
        exchange("async", tracking, recorder=recorder)
    events = recorder.report()["events"]
    assert [(e["kind"], e["outcome"]) for e in events] == [
        ("call", "started"),
        ("attempt", "started"),
        ("attempt", "cancelled"),
        ("call", "cancelled"),
    ]
    assert tracking.closed
    assert len(tracking.requests) == 1
