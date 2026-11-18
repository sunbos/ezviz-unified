"""Offline wire and validation tests for the domestic stream archive VOD facade.

``Client.vod`` and ``AsyncClient.vod`` are exercised on real clients whose
only mocked layer is the final HTTPX transport.  Auth and business payloads
are fully synthetic; nothing reaches the network and no pytest-asyncio
plugin is required.
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
from datetime import datetime, timezone

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    Client,
    DiagnosticRecorder,
    ProtocolError,
    RegionConfig,
    ResponsePolicy,
    decode_response,
)

APP_KEY = "PRIVATEKEY"
APP_SECRET = "PRIVATESECRET"
TOKEN = "PRIVATETOKEN"
STREAM_ID = "PRIVATE-Stream-9aB_mixed"  # non-32, mixed case: sent verbatim
NODE_ID = "PRIVATENODE"
# Full synthetic media URL plus sub-strings that catch truncation: asserting
# the whole URL is absent alone would miss a partially-leaked tail.
MEDIA_URL = "https://private.invalid/asset'clip/index.m3u8?ticket=URL_FIXTURE_PRIVATE&part=hidden"
MEDIA_URL_PARTS = ("URL_FIXTURE_PRIVATE", "&part=hidden")
PLAIN_URL = "https://docs.invalid/plain"
MODES = ("sync", "async")

SOURCE_PATH = "/api/service/media/streammanage/stream/file/info"
INDEX_PATH = "/api/service/media/streammanage/stream/index/file"

AUTH_BODY = {
    "code": "200",
    "data": {
        "accessToken": TOKEN,
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}


def _ok(data):
    return {"meta": {"code": 200, "message": "ok", "moreInfo": None}, "data": data}


SOURCE_DATA = {
    "streamId": STREAM_ID,
    "fileInfo": [{"fileNodeId": NODE_ID, "createTime": "0000123456789", "extra": None}],
}
INDEX_DATA = {
    "streamId": STREAM_ID,
    "fileInfo": {"fileNodeId": NODE_ID, "fileUrl": MEDIA_URL, "extra": 0},
}
PAYLOADS = {
    "stream_source_files": _ok(SOURCE_DATA),
    "stream_index_file": _ok(INDEX_DATA),
}
CALL_ARGS = {
    "stream_source_files": (
        (STREAM_ID,),
        {"file_type": 1, "query_date": "2024-02-29"},
    ),
    "stream_index_file": (
        (STREAM_ID,),
        {
            "start_time": datetime(2025, 7, 28, 23, 0, 0),
            "stop_time": datetime(2025, 7, 29, 23, 0, 0),
        },
    ),
}


def _events(recorder, name):
    # report()["events"] yields snapshot dicts; filter to the facade's
    # kind="call" scope for this exact entrypoint.
    return [
        e
        for e in recorder.report()["events"]
        if e["kind"] == "call" and e["entrypoint"] == "vod." + name
    ]


def _transports(recorder, region, biz_body):
    hits = {"auth": [], "biz": []}

    def auth(request):
        hits["auth"].append(request)
        return httpx.Response(200, json=AUTH_BODY)

    def call(request):
        hits["biz"].append(request)
        return httpx.Response(200, json=biz_body)

    options = {
        "region": region,
        "auth_transport": httpx.MockTransport(auth),
        "transport_factory": lambda origin: httpx.MockTransport(call),
        "diagnostic_recorder": recorder,
    }
    return options, hits


def _invoke(mode, facade, name, args, kwargs, options):
    if mode == "sync":
        with Client(APP_KEY, APP_SECRET, **options) as client:
            return getattr(getattr(client, facade), name)(*args, **kwargs)

    async def main():
        async with AsyncClient(APP_KEY, APP_SECRET, **options) as client:
            return await getattr(getattr(client, facade), name)(*args, **kwargs)

    return asyncio.run(main())


def _vod(mode, name, args, kwargs, *, payload=None):
    recorder = DiagnosticRecorder()
    body = PAYLOADS[name] if payload is None else payload
    options, hits = _transports(recorder, RegionConfig.china(), body)
    result = _invoke(mode, "vod", name, args, kwargs, options)
    return result, hits, recorder


def _reject(mode, name, args, kwargs, *, region=None, exc=(TypeError, ValueError)):
    recorder = DiagnosticRecorder()
    options, hits = _transports(recorder, region or RegionConfig.china(), {})
    with pytest.raises(exc):
        _invoke(mode, "vod", name, args, kwargs, options)
    assert hits["auth"] == [] and hits["biz"] == []
    events = _events(recorder, name)
    assert [e["outcome"] for e in events] == ["started", "failed"]
    assert all(e["mode"] == mode for e in events)


@pytest.mark.parametrize("mode", MODES)
def test_source_files_wire(mode):
    result, hits, recorder = _vod(mode, "stream_source_files", *CALL_ARGS["stream_source_files"])
    assert len(hits["auth"]) == 1 and len(hits["biz"]) == 1
    request = hits["biz"][0]
    assert request.method == "POST"
    assert request.url.path == SOURCE_PATH
    assert not request.url.query
    assert request.headers["accessToken"] == TOKEN
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    body = request.content.decode("utf-8")
    assert urllib.parse.parse_qs(body, keep_blank_values=True) == {
        "streamId": [STREAM_ID],
        "fileType": ["1"],
        "queryDate": ["2024-02-29"],
    }
    assert TOKEN not in body and TOKEN not in str(request.url)
    assert result.data == SOURCE_DATA
    assert result.raw == PAYLOADS["stream_source_files"]
    for secret in (APP_SECRET, TOKEN, STREAM_ID):
        assert secret not in recorder.to_json()
        assert secret not in repr(result)
    json.loads(recorder.to_json())
    events = _events(recorder, "stream_source_files")
    assert [e["outcome"] for e in events] == ["started", "succeeded"]
    assert all(e["mode"] == mode for e in events)


@pytest.mark.parametrize("mode", MODES)
def test_index_file_wire(mode):
    result, hits, _ = _vod(mode, "stream_index_file", *CALL_ARGS["stream_index_file"])
    assert len(hits["auth"]) == 1
    assert len(hits["biz"]) == 1  # returned media URL is never fetched
    request = hits["biz"][0]
    assert request.method == "POST"
    assert request.url.path == INDEX_PATH
    assert not request.url.query
    assert request.url.host != "private.invalid"
    assert request.headers["accessToken"] == TOKEN
    assert urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True) == {
        "streamId": [STREAM_ID],
        "startTime": ["2025-07-28 23:00:00"],
        "stopTime": ["2025-07-29 23:00:00"],
    }
    assert result.data == INDEX_DATA  # markup unchanged in decoded data
    assert result.data["fileInfo"]["fileUrl"] == MEDIA_URL
    assert result.raw == PAYLOADS["stream_index_file"]


SOURCE_VALID = [
    (1, "2024-02-29"),  # recording, leap day
    (2, None),  # snapshot, date omitted
    (2, "2025-07-28"),  # snapshot, date given
]
SOURCE_INVALID = [
    (1, None),  # recording requires query_date
    (True, "2025-07-28"),
    (False, None),
    ("1", "2025-07-28"),
    (3, "2025-07-28"),
    (0, "2025-07-28"),
    (1, "2024-02-30"),  # invalid date
    (1, "2024-2-9"),  # non-canonical
    (1, "2025-07-28 00:00:00"),
    (1, ""),
    (1, "   "),
    (1, b"\xff\xfe"),  # not a UTF-8 str
]
BAD_STREAM_IDS = ["", "   ", "\n", None, 0, 123, b"\xff\xfe"]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("file_type,query_date", SOURCE_VALID)
def test_source_valid_combinations(mode, file_type, query_date):
    kwargs = {"file_type": file_type}
    if query_date is not None:
        kwargs["query_date"] = query_date
    result, hits, _ = _vod(mode, "stream_source_files", (STREAM_ID,), kwargs)
    expected = {"streamId": [STREAM_ID], "fileType": [str(file_type)]}
    if query_date is not None:
        expected["queryDate"] = [query_date]
    form = urllib.parse.parse_qs(hits["biz"][0].content.decode("utf-8"), keep_blank_values=True)
    assert form == expected
    assert result.data == SOURCE_DATA


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("file_type,query_date", SOURCE_INVALID)
def test_source_invalid_combinations(mode, file_type, query_date):
    _reject(
        mode,
        "stream_source_files",
        (STREAM_ID,),
        {"file_type": file_type, "query_date": query_date},
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("stream_id", BAD_STREAM_IDS)
def test_source_bad_stream_id(mode, stream_id):
    _reject(mode, "stream_source_files", (stream_id,), {"file_type": 2})


INDEX_VALID = [
    (datetime(2025, 7, 28, 12, 0, 0), datetime(2025, 7, 28, 13, 0, 0)),
    (datetime(2025, 7, 28, 23, 0, 0), datetime(2025, 7, 29, 23, 0, 0)),  # 24h跨午夜
    (datetime(2025, 7, 28, 8, 30, 0), datetime(2025, 7, 28, 8, 30, 0)),  # 零时长
]
INDEX_INVALID = [
    (datetime(2025, 7, 29), datetime(2025, 7, 28)),  # reversed
    (datetime(2025, 7, 28), datetime(2025, 7, 29, 0, 0, 1)),  # over 24h
    (datetime(2025, 7, 28, 0, 0, 0, tzinfo=timezone.utc), datetime(2025, 7, 28, 1, 0, 0)),
    (datetime(2025, 7, 28, 0, 0, 0), datetime(2025, 7, 28, 1, 0, 0, tzinfo=timezone.utc)),
    (datetime(2025, 7, 28, 0, 0, 0, 500), datetime(2025, 7, 28, 1, 0, 0)),
    ("2025-07-28 00:00:00", datetime(2025, 7, 28, 1, 0, 0)),
    (None, datetime(2025, 7, 28, 1, 0, 0)),
    (datetime(2025, 7, 28, 0, 0, 0), "2025-07-28 01:00:00"),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("start,stop", INDEX_VALID)
def test_index_valid_ranges(mode, start, stop):
    result, hits, _ = _vod(
        mode,
        "stream_index_file",
        (STREAM_ID,),
        {"start_time": start, "stop_time": stop},
    )
    form = urllib.parse.parse_qs(hits["biz"][0].content.decode("utf-8"), keep_blank_values=True)
    assert form == {
        "streamId": [STREAM_ID],
        "startTime": [start.strftime("%Y-%m-%d %H:%M:%S")],
        "stopTime": [stop.strftime("%Y-%m-%d %H:%M:%S")],
    }
    assert result.data == INDEX_DATA


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("start,stop", INDEX_INVALID)
def test_index_invalid_ranges(mode, start, stop):
    _reject(
        mode,
        "stream_index_file",
        (STREAM_ID,),
        {"start_time": start, "stop_time": stop},
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name", ("stream_source_files", "stream_index_file"))
def test_overseas_rejected_before_network(mode, name):
    args, kwargs = CALL_ARGS[name]
    _reject(
        mode,
        name,
        args,
        kwargs,
        region=RegionConfig.overseas(),
        exc=(ApiError, ValueError),
    )


BAD_SHAPES = [
    ("stream_source_files", _ok([])),
    ("stream_source_files", _ok({"streamId": STREAM_ID, "fileInfo": {}})),
    ("stream_index_file", _ok([])),
    ("stream_index_file", _ok({"streamId": STREAM_ID, "fileInfo": []})),
]
EMPTY_SHAPES = [
    ("stream_source_files", _ok({"streamId": STREAM_ID, "fileInfo": []})),
    ("stream_index_file", _ok({"streamId": STREAM_ID, "fileInfo": {}})),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,payload", BAD_SHAPES)
def test_wrong_shapes_raise_protocol_error(mode, name, payload):
    args, kwargs = CALL_ARGS[name]
    with pytest.raises(ProtocolError):
        _vod(mode, name, args, kwargs, payload=payload)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("name,payload", EMPTY_SHAPES)
def test_empty_containers_allowed(mode, name, payload):
    args, kwargs = CALL_ARGS[name]
    result, _, _ = _vod(mode, name, args, kwargs, payload=payload)
    assert result.data == payload["data"]
    assert result.raw == payload


@pytest.mark.parametrize("mode", MODES)
def test_business_error_redacts_secrets_and_url(mode):
    payload = {
        "meta": {
            "code": 20005,
            "message": f"rejected {STREAM_ID} at {MEDIA_URL}",
            "moreInfo": None,
        },
        "data": {},
    }
    args, kwargs = CALL_ARGS["stream_index_file"]
    with pytest.raises(ApiError) as err:
        _vod(mode, "stream_index_file", args, kwargs, payload=payload)
    text = str(err.value) + "|" + repr(err.value)
    assert "20005" in text
    assert STREAM_ID not in text
    assert MEDIA_URL not in text
    # Truncation sentinels: any partially-rendered URL tail still leaks these.
    for part in MEDIA_URL_PARTS:
        assert part not in text
    assert APP_SECRET not in text and TOKEN not in text


@pytest.mark.parametrize("mode", MODES)
def test_default_policy_keeps_plain_url(mode):
    """Regression: operations without redact_message_urls leave URLs alone."""
    recorder = DiagnosticRecorder()
    body = {
        "code": "20005",
        "msg": f"see {PLAIN_URL}",
        "message": f"see {PLAIN_URL}",
        "data": {},
    }
    options, _ = _transports(recorder, RegionConfig.china(), body)
    with pytest.raises(ApiError) as err:
        _invoke(mode, "traffic", "account_summary", (), {}, options)
    text = str(err.value) + repr(err.value)
    assert "20005" in text
    assert PLAIN_URL in text


_DECODER_META_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
)
_DECODER_META_POLICY_REDACT = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    redact_message_urls=True,
)


@pytest.mark.parametrize(
    "payload,http_status,expect_code",
    [
        # HTTP 403 carrying a numeric business code; URLs masked only by opt-in.
        (
            {
                "meta": {
                    "code": 20005,
                    "message": f"denied {MEDIA_URL}",
                    "moreInfo": None,
                },
                "data": {},
            },
            403,
            20005,
        ),
        # HTTP 200 whose business *code* itself is a URL-containing string
        # (not a normal numeric status): the opt-in masks it in the rendered
        # exception while classification stays a plain business error.
        (
            {
                "meta": {
                    "code": f"failure at {MEDIA_URL}",
                    "message": "rejected",
                    "moreInfo": None,
                },
                "data": {},
            },
            200,
            None,
        ),
    ],
)
def test_decode_meta_policy_masks_url_parts_only_when_opted_in(payload, http_status, expect_code):
    """Real decode_response regression: URL masking is strictly opt-in."""
    with pytest.raises(ApiError) as leaked:
        decode_response(payload, policy=_DECODER_META_POLICY, http_status=http_status)
    text = str(leaked.value) + "|" + repr(leaked.value)
    assert MEDIA_URL in text
    for part in MEDIA_URL_PARTS:
        assert part in text

    with pytest.raises(ApiError) as masked:
        decode_response(payload, policy=_DECODER_META_POLICY_REDACT, http_status=http_status)
    text = str(masked.value) + "|" + repr(masked.value)
    assert MEDIA_URL not in text
    for part in MEDIA_URL_PARTS:
        assert part not in text
    if expect_code is not None:
        assert str(expect_code) in text


@pytest.mark.parametrize("flag", ["true", 1, None])
def test_policy_redact_message_urls_requires_bool(flag):
    with pytest.raises(ValueError):
        ResponsePolicy(redact_message_urls=flag)
