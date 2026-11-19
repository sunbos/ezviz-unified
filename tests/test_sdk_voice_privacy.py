"""Privacy checks for the domestic device-voice API (list + rename).

All transports are fully mocked; every credential, name and URL below is
synthetic and nothing leaves ``httpx.MockTransport``.
"""

import asyncio
import json
import logging
import re
import urllib.parse

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
    ResponsePolicy,
    TransportError,
)
from ezviz_unified.transport import AsyncTransport, SyncTransport

ACCESS_TOKEN = "SYNTH_TOKEN"
BASE = "https://open.ys7.com"
VOICE_PATH = "/api/route/voice/v3/devices/voices"
LIST_OP = "device_voice.list"
RENAME_OP = "device_voice.rename"

# Unique ASCII tails survive percent-encoding, so they catch raw AND
# encoded leaks of the submitted private values.
NAME_TAIL = "JXQ7Z4"
SIG_TAIL = "SG8K2P"
EXTRA_TAIL = "EX5N9Q"
SERIAL_TAIL = "XV94"
URL_MARK = "PRIVATE-MARK"

VOICE_NAME = "宝宝 私藏 & 名字 " + NAME_TAIL
VOICE_URL = "https://media.invalid/" + URL_MARK + "?sig=" + SIG_TAIL + "&extra=" + EXTRA_TAIL
DEVICE_SERIAL = "CSEPMR511&/=?" + SERIAL_TAIL
VOICE_ID = 12345


def _make_auth(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": ACCESS_TOKEN,
                    "expireTime": 9999999999999,
                    "areaDomain": BASE,
                },
            },
        )

    return handler


def _make_biz(seen, responder):
    def handler(request):
        seen.append(request)
        return responder(request)

    return handler


def _client(cls, responder, rec):
    auth_seen, biz_seen = [], []
    client = cls(
        "SYNTHKEY",
        "SYNTHSECRET",
        region=RegionConfig.china(),
        auth_transport=httpx.MockTransport(_make_auth(auth_seen)),
        transport_factory=lambda origin: httpx.MockTransport(_make_biz(biz_seen, responder)),
        diagnostic_recorder=rec,
    )
    return client, auth_seen, biz_seen


def _list_responder(request):
    return httpx.Response(
        200,
        json={
            "meta": {"code": 200, "message": "OK"},
            "voiceInfos": [
                {
                    "voiceId": VOICE_ID,
                    "voiceName": VOICE_NAME,
                    "voiceUrl": VOICE_URL,
                    "status": 1,
                    "createTime": "2025-01-02 03:04:05",
                    "unlabeled": "RESPONSEONLY-8F4",
                },
                {"voiceId": 22222, "voiceName": "public chime"},
            ],
        },
    )


def _rename_responder(request):
    return httpx.Response(
        200, json={"meta": {"code": 200, "message": "rename ok"}, "traceId": "TRACE-0R1"}
    )


def _error_responder(request):
    return httpx.Response(
        403,
        json={
            "meta": {"code": 10031, "message": "material rejected"},
            "requestUrl": BASE + "/response-only-R3J2",
        },
    )


def _forbidden():
    frags = {
        VOICE_NAME,
        VOICE_URL,
        DEVICE_SERIAL,
        ACCESS_TOKEN,
        "SYNTHKEY",
        "SYNTHSECRET",
        "CSEPMR511",
        "media.invalid",
        NAME_TAIL,
        SIG_TAIL,
        EXTRA_TAIL,
        SERIAL_TAIL,
        URL_MARK,
    }
    for value in (VOICE_NAME, VOICE_URL, DEVICE_SERIAL):
        frags.add(urllib.parse.quote_plus(value))
        frags.add(urllib.parse.quote(value))
        frags.add(urllib.parse.quote(value, safe=""))
    return frags


def _assert_clean(text, extra=()):
    for frag in _forbidden():
        assert frag not in text, frag
    for frag in extra:
        assert frag not in text, frag


def _httpx_text(caplog):
    return "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")


def _check_diagnostics(rec, operation, extra=()):
    report = rec.report()
    events = report["events"]
    attempts = [e for e in events if e.get("kind") == "attempt" and e.get("operation") == operation]
    assert attempts
    assert any(e.get("outcome") == "started" for e in attempts)
    assert any(e.get("outcome") not in (None, "started") for e in attempts)
    calls = [e for e in events if e.get("kind") == "call"]
    assert any(e.get("outcome") == "started" for e in calls)
    assert any(e.get("outcome") not in (None, "started") for e in calls)
    assert any(operation in (e.get("entrypoint") or "") for e in calls)
    for e in calls:
        assert e.get("entrypoint")
        assert e.get("mode")
    _assert_clean(json.dumps(report, ensure_ascii=False), extra=extra)


def _assert_no_business_body(request):
    assert request.content == b""
    ctype = request.headers.get("content-type", "")
    assert "json" not in ctype
    assert "urlencoded" not in ctype


def test_device_voice_list_sync(caplog):
    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, _list_responder, rec)
    with client:
        with caplog.at_level(logging.INFO, logger="httpx"):
            resp = client.device_voice.list(DEVICE_SERIAL)

    assert isinstance(resp, ApiResponse)
    assert resp.code == 200
    assert resp.message == "OK"
    assert resp.operation == LIST_OP
    # API output stays explicitly available, private rows included.
    assert "voiceInfos" in resp.raw
    assert resp.data == resp.raw["voiceInfos"]
    assert resp.data[0]["unlabeled"] == "RESPONSEONLY-8F4"
    assert resp.data[1] == {"voiceId": 22222, "voiceName": "public chime"}

    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    req = biz_seen[0]
    assert req.method == "GET"
    assert req.url.path == VOICE_PATH
    assert req.url.params["accessToken"] == ACCESS_TOKEN
    assert req.url.params["deviceSerial"] == DEVICE_SERIAL
    assert "voiceId" not in req.url.params
    assert "voiceName" not in req.url.params
    _assert_no_business_body(req)

    _assert_clean(repr(resp))
    _check_diagnostics(rec, LIST_OP)
    _assert_clean(_httpx_text(caplog))


def test_device_voice_list_without_voice_infos_sync():
    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(
        Client,
        lambda req: httpx.Response(200, json={"meta": {"code": 200, "message": "OK"}}),
        rec,
    )
    with client:
        # Frozen contract: a missing top-level voiceInfos is a protocol
        # failure, never fabricated into an empty array or None data.
        with pytest.raises(ProtocolError) as excinfo:
            client.device_voice.list(DEVICE_SERIAL)
    err = excinfo.value
    assert type(err) is ProtocolError
    assert err.operation == LIST_OP
    assert err.http_status == 200
    assert err.code is None
    assert len(auth_seen) == 1
    assert len(biz_seen) == 1


def test_device_voice_rename_sync(caplog):
    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, _rename_responder, rec)
    with client:
        with caplog.at_level(logging.INFO, logger="httpx"):
            resp = client.device_voice.rename(
                DEVICE_SERIAL,
                VOICE_ID,
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )
        # bool is not an int and must be rejected client-side, off the wire.
        with pytest.raises(ValueError):
            client.device_voice.rename(
                DEVICE_SERIAL,
                True,
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )

    assert isinstance(resp, ApiResponse)
    assert resp.code == 200
    assert resp.message == "rename ok"
    assert resp.operation == RENAME_OP
    assert resp.raw["traceId"] == "TRACE-0R1"

    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    req = biz_seen[0]
    assert req.method == "PUT"
    assert req.url.path == VOICE_PATH
    params = req.url.params
    assert params["accessToken"] == ACCESS_TOKEN
    assert params["deviceSerial"] == DEVICE_SERIAL
    assert params["voiceId"] == str(VOICE_ID)
    assert params["voiceName"] == VOICE_NAME
    assert params["voiceUrl"] == VOICE_URL
    _assert_no_business_body(req)

    tagged = "voiceId=" + str(VOICE_ID)
    _assert_clean(repr(resp), extra=[tagged])
    _check_diagnostics(rec, RENAME_OP, extra=[tagged])
    _assert_clean(_httpx_text(caplog), extra=[tagged])


def test_device_voice_error_sync(caplog):
    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, _error_responder, rec)
    with client:
        with caplog.at_level(logging.INFO, logger="httpx"):
            with pytest.raises(ApiError) as excinfo:
                client.device_voice.rename(
                    DEVICE_SERIAL,
                    1,
                    voice_name=VOICE_NAME,
                    voice_url=VOICE_URL,
                )
            logging.getLogger("httpx").info("external-example-ok")

    err = excinfo.value
    parts = [str(err), repr(err), str(getattr(err, "__dict__", {}))]
    for attr in ("message", "code", "status", "status_code", "operation"):
        value = getattr(err, attr, None)
        if value is not None:
            parts.append(str(value))
    rendered = " | ".join(parts)
    _assert_clean(rendered, extra=["voiceId=1"])
    # The bare digit "1" must not eat the numeric business code or HTTP status.
    assert "10031" in rendered
    assert "403" in rendered
    assert "material rejected" in rendered

    assert len(auth_seen) == 1
    assert len(biz_seen) == 1  # PUT is never retried
    _check_diagnostics(rec, RENAME_OP, extra=["voiceId=1"])
    _assert_clean(_httpx_text(caplog), extra=["voiceId=1"])
    # An unrelated httpx INFO record still logs verbatim after the failure.
    assert any("external-example-ok" in r.getMessage() for r in caplog.records if r.name == "httpx")


def test_device_voice_async(caplog):
    async def main():
        rec = DiagnosticRecorder()
        auth_seen, biz_seen = [], []

        def biz(request):
            biz_seen.append(request)
            if request.method == "GET":
                return _list_responder(request)
            if request.url.params.get("voiceId") == "1":
                return _error_responder(request)
            return _rename_responder(request)

        client = AsyncClient(
            "SYNTHKEY",
            "SYNTHSECRET",
            region=RegionConfig.china(),
            auth_transport=httpx.MockTransport(_make_auth(auth_seen)),
            transport_factory=lambda origin: httpx.MockTransport(biz),
            diagnostic_recorder=rec,
        )
        async with client:
            listed = await client.device_voice.list(DEVICE_SERIAL)
            renamed = await client.device_voice.rename(
                DEVICE_SERIAL,
                VOICE_ID,
                voice_name=VOICE_NAME,
                voice_url=VOICE_URL,
            )
            err = None
            try:
                await client.device_voice.rename(
                    DEVICE_SERIAL,
                    1,
                    voice_name=VOICE_NAME,
                    voice_url=VOICE_URL,
                )
            except ApiError as exc:
                err = exc
        return rec, auth_seen, biz_seen, listed, renamed, err

    with caplog.at_level(logging.INFO, logger="httpx"):
        rec, auth_seen, biz_seen, listed, renamed, err = asyncio.run(main())
        logging.getLogger("httpx").info("external-example-ok")

    assert listed.operation == LIST_OP
    assert "voiceInfos" in listed.raw
    assert listed.data[0]["voiceName"] == VOICE_NAME
    assert renamed.operation == RENAME_OP
    assert renamed.code == 200
    assert isinstance(err, ApiError)

    assert [r.method for r in biz_seen] == ["GET", "PUT", "PUT"]
    assert len(auth_seen) >= 1
    get_req, put_req, _ = biz_seen
    assert get_req.url.path == VOICE_PATH
    assert get_req.url.params["deviceSerial"] == DEVICE_SERIAL
    _assert_no_business_body(get_req)
    params = put_req.url.params
    assert params["accessToken"] == ACCESS_TOKEN
    assert params["deviceSerial"] == DEVICE_SERIAL
    assert params["voiceId"] == str(VOICE_ID)
    assert params["voiceName"] == VOICE_NAME
    assert params["voiceUrl"] == VOICE_URL
    _assert_no_business_body(put_req)

    tagged = "voiceId=" + str(VOICE_ID)
    _assert_clean(repr(listed), extra=[tagged])
    _assert_clean(repr(renamed), extra=[tagged])
    _assert_clean(str(err) + repr(err), extra=[tagged, "voiceId=1"])
    _check_diagnostics(rec, LIST_OP, extra=[tagged])
    _check_diagnostics(rec, RENAME_OP, extra=[tagged])
    _assert_clean(_httpx_text(caplog), extra=[tagged])
    assert any("external-example-ok" in r.getMessage() for r in caplog.records if r.name == "httpx")


# --- Privacy cross-review regressions -------------------------------------
#
# (a) A voiceId echoed in an httpx log record and in the backend message must
# be masked as a labelled sensitive value, while the numeric business code
# and the HTTP status stay intact.

VOICE_ID_LEAK = 91


def _voice_id_leak_responder(request):
    # Logged inside the in-flight request context, so the SDK's scoped
    # redaction filter sees it; an isolated "voiceId=91" label is masked.
    logging.getLogger("httpx").info("event voiceId=91 preserved")
    return httpx.Response(
        200,
        json={
            "meta": {
                "code": 10031,
                "message": "material rejected voiceId=91",
            }
        },
    )


def _scoped_httpx_text(caplog, outside_message):
    # All httpx records except the explicitly logged outside-context one.
    return "\n".join(
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx" and r.getMessage() != outside_message
    )


def test_device_voice_voice_id_not_leaked_sync(caplog):
    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, _voice_id_leak_responder, rec)
    with client:
        with caplog.at_level(logging.INFO, logger="httpx"):
            with pytest.raises(ApiError) as excinfo:
                client.device_voice.rename(
                    DEVICE_SERIAL,
                    VOICE_ID_LEAK,
                    voice_name="voice",
                    voice_url=VOICE_URL,
                )
            logging.getLogger("httpx").info("outside-after-failure voiceId=91")

    err = excinfo.value
    rendered = " | ".join([str(err), repr(err), err.message, str(getattr(err, "__dict__", {}))])
    # The submitted voiceId survives only as a masked label, never isolated.
    assert not re.search(r"(?<!\d)91(?!\d)", rendered)
    assert err.code == 10031
    assert "10031" in rendered
    assert "material rejected" in rendered

    assert len(auth_seen) == 1
    assert len(biz_seen) == 1  # PUT is never retried
    # The SDK-scope redaction filter only masks records logged while a
    # request is in flight; the outside-context record keeps its verbatim
    # "voiceId=91" and is checked separately, so the clean/no-91 checks
    # below cover just the in-scope stream.
    sdk_text = _scoped_httpx_text(caplog, "outside-after-failure voiceId=91")
    _assert_clean(sdk_text, extra=["voiceId=91"])
    # Isolated "91" must not appear even inside the partially masked label.
    assert not re.search(r"(?<!\d)91(?!\d)", sdk_text)
    # Logging outside the SDK request context is not redacted by the SDK:
    # that one record keeps its exact submitted text.
    outside = [
        r.getMessage()
        for r in caplog.records
        if r.name == "httpx" and "outside-after-failure" in r.getMessage()
    ]
    assert outside == ["outside-after-failure voiceId=91"]
    _check_diagnostics(rec, RENAME_OP, extra=["voiceId=91"])


@pytest.mark.parametrize("http_status", [200, 403])
def test_device_voice_echoed_values_not_leaked_sync(http_status, caplog):
    # A unique private prefix embedded in a submitted URL inside voice_name,
    # plus a signed voice_url with nonascii/space/& and a distinctive second
    # query field. The backend echoes raw values plus percent-encoded forms
    # in a nonnumeric code and in meta.message; a response-only URL rides
    # along. All of it must stay out of the caller-visible error surface.
    prefix = "PRIVATE_NAME_MARKER"
    url_tail = "PRIVNAMETAIL9K"
    sig_tail = "SIGTAIL3XD"
    second_tail = "SECONDQF7W"
    name = f"{prefix} https://example.invalid/private-name-url-{url_tail}"
    voice_url = f"https://media.invalid/signed/私藏 路径&名?sig={sig_tail}&second={second_tail}"
    resp_url = f"https://resp.invalid/only-{url_tail}-RSPONLY"
    quoted_name = urllib.parse.quote_plus(name, safe="")
    quoted_url = urllib.parse.quote(voice_url, safe="")
    echo = (
        f"rejected name={name} url={voice_url} enc1={quoted_name} enc2={quoted_url} see={resp_url}"
    )
    # The mocked platform code echoes the full original submitted name and
    # URL (raw plus percent-encoded), not an invented substring composite:
    # every byte below is a registered request secret, so it is masked on
    # all caller-visible surfaces.
    echoed_code = f"ERR {name} {voice_url} {quoted_url}"

    def responder(request):
        return httpx.Response(
            http_status,
            json={
                "meta": {
                    "code": echoed_code,
                    "message": "rejected " + echo,
                }
            },
        )

    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, responder, rec)
    with client:
        with caplog.at_level(logging.INFO, logger="httpx"):
            with pytest.raises(ApiError) as excinfo:
                client.device_voice.rename(
                    DEVICE_SERIAL,
                    7,
                    voice_name=name,
                    voice_url=voice_url,
                )
            logging.getLogger("httpx").info("external-example-ok")

    err = excinfo.value
    surface = " | ".join(
        [str(err), repr(err), err.message, str(err.code), str(getattr(err, "__dict__", {}))]
    )
    forbidden = {
        name,
        voice_url,
        prefix,
        url_tail,
        sig_tail,
        second_tail,
        "example.invalid",
        "media.invalid",
        "resp.invalid",
        "private-name-url",
        "RSPONLY",
        urllib.parse.quote_plus(name, safe=""),
        urllib.parse.quote(name, safe=""),
        urllib.parse.quote_plus(voice_url, safe=""),
        urllib.parse.quote(voice_url, safe=""),
        ACCESS_TOKEN,
        DEVICE_SERIAL,
        "SYNTHKEY",
        "SYNTHSECRET",
    }
    for frag in forbidden:
        assert frag not in surface, frag
    # Benign context survives masking.
    assert "rejected" in err.message
    if http_status == 403:
        assert err.http_status == 403

    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    report_text = json.dumps(rec.report(), ensure_ascii=False)
    for frag in forbidden:
        assert frag not in report_text, frag
    httpx_text = _httpx_text(caplog)
    for frag in forbidden:
        assert frag not in httpx_text, frag
    assert any("external-example-ok" in r.getMessage() for r in caplog.records if r.name == "httpx")


@pytest.mark.parametrize("http_status", [200, 403])
def test_device_voice_echoed_values_not_leaked_async(http_status):
    prefix = "PRIVATE_NAME_MARKER"
    url_tail = "PRIVNAMETAIL9K"
    sig_tail = "SIGTAIL3XD"
    second_tail = "SECONDQF7W"
    name = f"{prefix} https://example.invalid/private-name-url-{url_tail}"
    voice_url = f"https://media.invalid/signed/私藏 路径&名?sig={sig_tail}&second={second_tail}"
    resp_url = f"https://resp.invalid/only-{url_tail}-RSPONLY"
    echo = (
        f"rejected name={name} url={voice_url} "
        f"enc1={urllib.parse.quote_plus(name, safe='')} "
        f"enc2={urllib.parse.quote(voice_url, safe='')} see={resp_url}"
    )
    # Same contract as the sync case: the code echoes the full submitted
    # values, which are registered secrets and thus masked on render.
    echoed_code = f"ERR {name} {voice_url} {urllib.parse.quote(voice_url, safe='')}"

    async def main():
        rec = DiagnosticRecorder()
        auth_seen, biz_seen = [], []

        def biz(request):
            biz_seen.append(request)
            return httpx.Response(
                http_status,
                json={
                    "meta": {
                        "code": echoed_code,
                        "message": "rejected " + echo,
                    }
                },
            )

        client = AsyncClient(
            "SYNTHKEY",
            "SYNTHSECRET",
            region=RegionConfig.china(),
            auth_transport=httpx.MockTransport(_make_auth(auth_seen)),
            transport_factory=lambda origin: httpx.MockTransport(biz),
            diagnostic_recorder=rec,
        )
        err = None
        async with client:
            try:
                await client.device_voice.rename(
                    DEVICE_SERIAL,
                    7,
                    voice_name=name,
                    voice_url=voice_url,
                )
            except ApiError as exc:
                err = exc
        return rec, auth_seen, biz_seen, err

    rec, auth_seen, biz_seen, err = asyncio.run(main())
    assert isinstance(err, ApiError)
    surface = " | ".join(
        [str(err), repr(err), err.message, str(err.code), str(getattr(err, "__dict__", {}))]
    )
    forbidden = {
        name,
        voice_url,
        prefix,
        url_tail,
        sig_tail,
        second_tail,
        "example.invalid",
        "media.invalid",
        "resp.invalid",
        "private-name-url",
        "RSPONLY",
        urllib.parse.quote_plus(name, safe=""),
        urllib.parse.quote(name, safe=""),
        urllib.parse.quote_plus(voice_url, safe=""),
        urllib.parse.quote(voice_url, safe=""),
        ACCESS_TOKEN,
        DEVICE_SERIAL,
        "SYNTHKEY",
        "SYNTHSECRET",
    }
    for frag in forbidden:
        assert frag not in surface, frag
    assert "rejected" in err.message
    if http_status == 403:
        assert err.http_status == 403
    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    report_text = json.dumps(rec.report(), ensure_ascii=False)
    for frag in forbidden:
        assert frag not in report_text, frag


@pytest.mark.parametrize("http_status", [200, 403])
def test_device_voice_foreign_provider_code_not_recorded_sync(http_status):
    # An arbitrary provider-invented code string is opaque to the SDK: the
    # caller-visible error carries it verbatim, but it is not a submitted
    # secret and must never be mistaken for one. The DiagnosticRecorder and
    # its serialized diagnostic surface only store the whitelisted
    # code_category ("other"), never the raw code text.
    url_tail = "PRIVNAMETAIL9K"
    second_tail = "SECONDQF7W"
    foreign_code = f"ERR-{url_tail}-{second_tail}"

    def responder(request):
        return httpx.Response(
            http_status,
            json={
                "meta": {
                    "code": foreign_code,
                    "message": "rejected benign context",
                }
            },
        )

    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, responder, rec)
    with client:
        with pytest.raises(ApiError) as excinfo:
            client.device_voice.rename(
                DEVICE_SERIAL,
                7,
                voice_name="voice",
                voice_url=VOICE_URL,
            )

    err = excinfo.value
    # The foreign code is opaque platform data on the error object; the SDK
    # does not invent privacy claims over freestanding substrings inside a
    # separately invented provider string.
    assert err.code == foreign_code
    assert len(auth_seen) == 1
    assert len(biz_seen) == 1
    # Diagnostics never store raw codes: only the whitelisted category.
    report = rec.report()
    for event in report["events"]:
        assert "code" not in event
        if event.get("outcome") == "failed":
            assert event.get("code_category") == "other"
    serialized = rec.to_json()
    assert foreign_code not in serialized
    assert url_tail not in serialized
    assert second_tail not in serialized
    assert foreign_code not in json.dumps(report, ensure_ascii=False)


def test_device_voice_10002_classification_overlapping_digits():
    # An unrelated submitted string containing "10002" must not corrupt the
    # genuine 10002 classification or the invalid_token marker.
    name = "batch10002marker"

    def responder(request):
        return httpx.Response(
            200,
            json={"meta": {"code": 10002, "message": f"token invalid {name}"}},
        )

    rec = DiagnosticRecorder()
    client, auth_seen, biz_seen = _client(Client, responder, rec)
    with client:
        with pytest.raises(AuthenticationError) as excinfo:
            client.device_voice.rename(
                DEVICE_SERIAL,
                7,
                voice_name=name,
                voice_url=VOICE_URL,
            )
    err = excinfo.value
    assert err.invalid_token is True
    assert err.code == 10002
    assert len(biz_seen) == 1  # PUT is never retried even on 10002


# (c) Closed low-level transports reject request() before touching the wire,
# even when params carry an accessToken plus a lone surrogate under an
# opted-in URL-masking policy (the new request-encoding order).

_CLOSED_POLICY = ResponsePolicy(redact_message_urls=True)


def test_closed_transport_rejects_request_sync():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "200"})

    transport = SyncTransport("https://api.example.test", transport=httpx.MockTransport(handler))
    transport.close()
    with pytest.raises(TransportError) as excinfo:
        transport.request(
            "GET",
            "/api",
            params={"accessToken": "SYNTH_CLOSED_TOKEN", "note": "\ud800"},
            policy=_CLOSED_POLICY,
            operation="device_voice.list",
        )
    assert excinfo.value.reason == "closed"
    assert calls == []


def test_closed_transport_rejects_request_async():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"code": "200"})

    async def main():
        transport = AsyncTransport(
            "https://api.example.test", transport=httpx.MockTransport(handler)
        )
        await transport.aclose()
        with pytest.raises(TransportError) as excinfo:
            await transport.request(
                "GET",
                "/api",
                params={"accessToken": "SYNTH_CLOSED_TOKEN", "note": "\ud800"},
                policy=_CLOSED_POLICY,
                operation="device_voice.list",
            )
        return excinfo.value

    err = asyncio.run(main())
    assert err.reason == "closed"
    assert calls == []
