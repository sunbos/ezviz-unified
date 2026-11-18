"""Offline wire and validation tests for the domestic message-queue facades.

``MessageSubscriptions`` and ``AsyncMessageSubscriptions`` are exercised
directly against real ``Client``/``AsyncClient`` instances whose transports
are ``httpx.MockTransport`` objects with fully synthetic authentication.
Nothing reaches the network and no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import urllib.parse

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    RegionConfig,
)
from ezviz_unified.message_subscriptions import (
    AsyncMessageSubscriptions,
    MessageSubscriptions,
)

APP_KEY = "TEST_APP_KEY"
APP_SECRET = "TEST_APP_SECRET"
TOKEN = "TEST_TOKEN"
CONSUMER_ID = "TEST_CONSUMER_ID"

GROUPS = ("group1", "group2", "group3", "group4", "group5")
CONSUME_PATH = "/api/lapp/mq/v2/consumer/messages"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
CREATE_BODY = {
    "code": "200",
    "msg": "ok",
    "data": {"consumerId": CONSUMER_ID, "extraField": "kept"},
    "rawExtra": "preserved",
}
CONSUME_BODY = {
    "code": "200",
    "msg": "ok",
    "data": [
        {"id": "m-1", "content": "payload-1", "unknown": 7},
        {"id": "m-2"},
    ],
    "rawExtra": "preserved",
}

METHOD_CASES = (
    ("create_consumer", ("group1",), {}),
    ("consume", (CONSUMER_ID,), {}),
)

BAD_GROUPS = (
    "group0",
    "group6",
    "group10",
    "GROUP1",
    "Group1",
    " group1",
    "group1 ",
    "",
    "group",
    0,
    1,
    True,
    None,
    b"group1",
)

BAD_CONSUMERS = (
    (None, "consumerId must be a non-blank string"),
    (123, "consumerId must be a non-blank string"),
    (True, "consumerId must be a non-blank string"),
    (b"cid", "consumerId must be a non-blank string"),
    ("", "consumerId must be a non-blank string"),
    ("   ", "consumerId must be a non-blank string"),
    ("cid\ud800", "consumerId must be encodable as UTF-8"),
)

BAD_OFFSETS = (True, False, "3", 2.5, b"4", "offset", object())

OPAQUE_CONSUMERS = ("  spaced  ", "消费者１", "cid-9_X", "0")

_MISSING = object()


def _region(label: str) -> RegionConfig:
    """Return the real RegionConfig for ``label`` ("domestic"/"overseas")."""
    if label == "domestic":
        return RegionConfig.china()
    return RegionConfig.overseas()


def _ok(payload):
    return lambda request: httpx.Response(200, json=dict(payload))


class _Harness:
    """Synthetic auth and business transports wrapped around a real client."""

    def __init__(self, *, region="domestic", responder=None):
        self.region = region
        self.responder = responder or _ok(CREATE_BODY)
        self.auth_calls = []
        self.business_calls = []
        self.factory_calls = []

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _handle_business(self, request):
        self.business_calls.append(request)
        return self.responder(request)

    def _factory(self, origin):
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def _kwargs(self):
        return {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "region": _region(self.region),
            "clock": lambda: 1000.0,
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
        }

    def sync_client(self):
        return Client(**self._kwargs())

    def async_client(self):
        return AsyncClient(**self._kwargs())


def _drive_sync(harness, name, args, kwargs):
    client = harness.sync_client()
    facade = MessageSubscriptions(client)
    try:
        return getattr(facade, name)(*args, **kwargs)
    finally:
        client.close()


async def _drive_async_inner(harness, name, args, kwargs):
    client = harness.async_client()
    facade = AsyncMessageSubscriptions(client)
    try:
        return await getattr(facade, name)(*args, **kwargs)
    finally:
        await client.aclose()


def _drive(kind, harness, name, args, kwargs):
    """Call facade method ``name`` on a real client, always closing it."""
    if kind == "sync":
        return _drive_sync(harness, name, args, kwargs)
    return asyncio.run(_drive_async_inner(harness, name, args, kwargs))


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _assert_create_wire(request, group):
    assert request.method == "POST"
    assert request.url.path == "/api/lapp/mq/v2/consumer/" + group
    assert not request.url.query
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # Token travels only in the form body, never in headers.
    assert "accessToken" not in request.headers
    assert "Authorization" not in request.headers
    # Only the token is sent in the body; the group lives in the path.
    assert _form(request) == {"accessToken": [TOKEN]}


def _assert_consume_wire(request, consumer_id, *, offset=_MISSING):
    assert request.method == "POST"
    assert request.url.path == CONSUME_PATH
    assert not request.url.query
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # Token travels only in the form body, never in headers.
    assert "accessToken" not in request.headers
    assert "Authorization" not in request.headers
    expected = {"accessToken": [TOKEN], "consumerId": [consumer_id]}
    if offset is not _MISSING:
        expected["offset"] = [str(offset)]
    assert _form(request) == expected


def _assert_create_response(response):
    assert response.http_status == 200
    assert response.code == "200"
    assert response.message == "ok"
    assert response.data == CREATE_BODY["data"]
    # ``raw`` is the original response dict, preserved verbatim.
    assert response.raw == CREATE_BODY
    assert response.raw["rawExtra"] == "preserved"
    assert response.data["extraField"] == "kept"


def _assert_consume_response(response):
    assert response.http_status == 200
    assert response.code == "200"
    assert response.message == "ok"
    # The service returns a message array; ``data`` is preserved as a list.
    assert response.data == CONSUME_BODY["data"]
    assert response.raw == CONSUME_BODY
    assert response.data[0]["unknown"] == 7


def _assert_no_io(harness):
    assert harness.factory_calls == []
    assert harness.auth_calls == []
    assert harness.business_calls == []


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("group", GROUPS)
def test_create_consumer_wire(kind, group):
    harness = _Harness()
    response = _drive(kind, harness, "create_consumer", (group,), {})
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _assert_create_wire(harness.business_calls[0], group)
    _assert_create_response(response)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("offset", (None, 0, -7, 98765432109876543210))
def test_consume_wire(kind, offset):
    harness = _Harness(responder=_ok(CONSUME_BODY))
    kwargs = {} if offset is None else {"offset": offset}
    response = _drive(kind, harness, "consume", (CONSUMER_ID,), kwargs)
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    marker = _MISSING if offset is None else offset
    _assert_consume_wire(harness.business_calls[0], CONSUMER_ID, offset=marker)
    _assert_consume_response(response)


@pytest.mark.parametrize("kind", ("sync", "async"))
def test_consume_explicit_none_offset_is_omitted(kind):
    harness = _Harness(responder=_ok(CONSUME_BODY))
    _drive(kind, harness, "consume", (CONSUMER_ID,), {"offset": None})
    assert len(harness.business_calls) == 1
    _assert_consume_wire(harness.business_calls[0], CONSUMER_ID)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("consumer_id", OPAQUE_CONSUMERS)
def test_consume_consumer_id_passed_verbatim(kind, consumer_id):
    harness = _Harness(responder=_ok(CONSUME_BODY))
    _drive(kind, harness, "consume", (consumer_id,), {})
    _assert_consume_wire(harness.business_calls[0], consumer_id)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("name,args,kwargs", METHOD_CASES)
def test_region_guard_rejects_overseas(kind, name, args, kwargs):
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError, match="domestic"):
        _drive(kind, harness, name, args, kwargs)
    _assert_no_io(harness)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize(
    "name,args,kwargs",
    (
        ("create_consumer", ("group9",), {}),
        ("consume", ("",), {}),
        ("consume", (CONSUMER_ID,), {"offset": "x"}),
    ),
)
def test_region_guard_runs_before_param_validation(kind, name, args, kwargs):
    harness = _Harness(region="overseas")
    with pytest.raises(ValueError, match="domestic"):
        _drive(kind, harness, name, args, kwargs)
    _assert_no_io(harness)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("group", BAD_GROUPS)
def test_create_consumer_rejects_bad_group(kind, group):
    harness = _Harness()
    with pytest.raises(ValueError) as excinfo:
        _drive(kind, harness, "create_consumer", (group,), {})
    assert str(excinfo.value) == "group must be exactly one of group1..group5"
    _assert_no_io(harness)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("consumer_id,message", BAD_CONSUMERS)
def test_consume_rejects_bad_consumer_id(kind, consumer_id, message):
    harness = _Harness()
    with pytest.raises(ValueError) as excinfo:
        _drive(kind, harness, "consume", (consumer_id,), {})
    assert str(excinfo.value) == message
    _assert_no_io(harness)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("offset", BAD_OFFSETS)
def test_consume_rejects_bad_offset(kind, offset):
    harness = _Harness()
    with pytest.raises(ValueError) as excinfo:
        _drive(kind, harness, "consume", (CONSUMER_ID,), {"offset": offset})
    assert str(excinfo.value) == "offset must be an integer"
    _assert_no_io(harness)


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("name,args,kwargs", METHOD_CASES)
def test_token_invalid_raises_without_replay(kind, name, args, kwargs):
    harness = _Harness(responder=_ok({"code": "10002", "msg": "accessToken expired"}))
    with pytest.raises(AuthenticationError) as excinfo:
        _drive(kind, harness, name, args, kwargs)
    assert type(excinfo.value) is AuthenticationError
    assert excinfo.value.invalid_token is True
    assert excinfo.value.code == "10002"
    assert excinfo.value.http_status == 200
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("name,args,kwargs", METHOD_CASES)
def test_api_error_raises_without_replay(kind, name, args, kwargs):
    harness = _Harness(responder=_ok({"code": "50001", "msg": "service error", "data": None}))
    with pytest.raises(ApiError) as excinfo:
        _drive(kind, harness, name, args, kwargs)
    assert type(excinfo.value) is ApiError
    assert excinfo.value.code == "50001"
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("name,args,kwargs", METHOD_CASES)
def test_http_error_raises_without_replay(kind, name, args, kwargs):
    harness = _Harness(responder=lambda request: httpx.Response(503, text="upstream unavailable"))
    with pytest.raises(ApiError) as excinfo:
        _drive(kind, harness, name, args, kwargs)
    assert type(excinfo.value) is ApiError
    assert excinfo.value.http_status == 503
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1


@pytest.mark.parametrize("kind", ("sync", "async"))
@pytest.mark.parametrize("name,args,kwargs", METHOD_CASES)
def test_int_code_is_error(kind, name, args, kwargs):
    harness = _Harness(responder=_ok({"code": 200, "msg": "ok", "data": {}}))
    with pytest.raises(ApiError) as excinfo:
        _drive(kind, harness, name, args, kwargs)
    assert type(excinfo.value) is ApiError
    assert excinfo.value.code == 200
    assert excinfo.value.http_status == 200
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
