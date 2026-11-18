"""Offline MockTransport tests for ``examples/message_subscriptions.py``.

Every test drives real ``Client``/``AsyncClient`` instances; only the
HTTP transport layer is mocked -- SDK business methods are never
patched. All values are synthetic and nothing reaches the network.
The helpers under test borrow a caller-owned client: they must create
exactly one consumer, read at most ``max_batches`` offset-free batches
and complete each mandatory handler before the next read.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import socket
import urllib.parse

import httpx
import pytest

import examples.message_subscriptions as ms
from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    Client,
    RegionConfig,
    TransportError,
)

APP_KEY = "APP_KEY_TEST"
APP_SECRET = "APP_SECRET_TEST"
TOKEN = "TEST_TOKEN"
CONSUMER_ID = "CID_OPAQUE_7fZ"
GROUP = "group2"
SENTINEL = "SENTINEL_9xQ"

GROUPS = ("group1", "group2", "group3", "group4", "group5")
CREATE_PATHS = {"/api/lapp/mq/v2/consumer/" + group for group in GROUPS}
CREATE_PATH = "/api/lapp/mq/v2/consumer/" + GROUP
CONSUME_PATH = "/api/lapp/mq/v2/consumer/messages"
AUTH_PATH = "/api/lapp/token/get"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
CREATE_DATA = {"consumerId": CONSUMER_ID, "extraField": "kept"}
BATCH_DATA = [
    {"id": "m-1", "content": "payload-1", "unknown": 7},
    {"id": "m-2"},
]

# Private marker distinguishing "no argument given" from an explicit
# ``None``/``"text"``/``[]`` payload: those shapes must reach the mock
# unchanged so the SDK can reject them, not be smoothed over by dict().
_UNSET = object()


def _lapp(data, *, code="200"):
    """LAPP envelope: top-level string ``code`` plus opaque ``data``."""
    return {"code": code, "msg": "ok", "data": data, "rawExtra": "preserved"}


def _create_body(data=_UNSET):
    # ``data`` is wrapped verbatim: an explicit None/str/list must arrive
    # as-is so the helper under test sees the real malformed shape.
    return _lapp(CREATE_DATA if data is _UNSET else data)


def _consume_body(data=_UNSET):
    return _lapp(BATCH_DATA if data is _UNSET else data)


class _Harness:
    """Auth + business transports recording requests; no stubbed SDK methods.

    ``business`` is an optional callable deciding each business response;
    the default serves one create body then one consume body per request.
    """

    def __init__(self, *, business=None, auth=None, trace=None):
        self.auth_calls: list[httpx.Request] = []
        self.business_calls: list[httpx.Request] = []
        self.factory_calls: list[str] = []
        self.trace = trace
        self._business = business if business is not None else self._business_default
        self._auth = auth if auth is not None else self._auth_default

    def _auth_default(self, request):
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _business_default(self, request):
        path = request.url.path
        # Every legal group create path is routed explicitly; anything else
        # (wrong group, wrong prefix) still fails loudly.
        if path in CREATE_PATHS:
            return httpx.Response(200, json=_create_body())
        if path == CONSUME_PATH:
            return httpx.Response(200, json=_consume_body())
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return self._auth(request)

    def _handle_business(self, request):
        self.business_calls.append(request)
        if self.trace is not None:
            self.trace.append(("http", request.url.path))
        return self._business(request)

    def _factory(self, origin: str) -> httpx.MockTransport:
        # The factory itself is synchronous and must receive the origin.
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def kwargs(self):
        return {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "region": RegionConfig.china(),
            "clock": lambda: 1000.0,
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
        }

    def sync_client(self) -> Client:
        return Client(**self.kwargs())

    def async_client(self) -> AsyncClient:
        return AsyncClient(**self.kwargs())


def _form(request) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _check_create(request, group=GROUP):
    assert request.method == "POST"
    assert request.url.path == "/api/lapp/mq/v2/consumer/" + group
    assert not request.url.query
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    # Token travels only in the form body, never in headers.
    assert "accessToken" not in request.headers
    assert _form(request) == {"accessToken": [TOKEN]}


def _check_consume(request, consumer_id=CONSUMER_ID):
    assert request.method == "POST"
    assert request.url.path == CONSUME_PATH
    assert not request.url.query
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert "accessToken" not in request.headers
    # No offset is ever sent: only the token and the verbatim consumer id.
    assert _form(request) == {"accessToken": [TOKEN], "consumerId": [consumer_id]}


def _run(mode, harness, sync_fn, async_fn):
    """Dispatch one scenario to a real client of the matching kind."""
    if mode == "sync":
        client = harness.sync_client()
        try:
            return sync_fn(client)
        finally:
            client.close()

    async def drive():
        client = harness.async_client()
        try:
            return await async_fn(client)
        finally:
            await client.aclose()

    return asyncio.run(drive())


def _set_env(monkeypatch, **overrides):
    values = {
        "EZVIZ_APP_KEY": APP_KEY,
        "EZVIZ_APP_SECRET": APP_SECRET,
        "EZVIZ_REGION": "domestic",
        "EZVIZ_MESSAGE_GROUP": GROUP,
    }
    values.update(overrides)
    # The auth base override must never leak into the domestic config path.
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    for key, value in values.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def _install(monkeypatch, harness, mode):
    """Swap the example's client class for a factory returning a real client.

    The injected factory builds a genuine SDK ``Client``/``AsyncClient``
    backed by the MockTransport harness -- SDK methods stay real.
    """
    client = harness.sync_client() if mode == "sync" else harness.async_client()
    target = ms.Client if mode == "sync" else ms.AsyncClient
    monkeypatch.setattr(ms, target.__name__, lambda *a, **kw: client)
    return client


def _read_report(capsys):
    out, err = capsys.readouterr()
    assert err == ""
    return json.loads(out)


def _assert_fixed_shape(report):
    """Every CLI report is drawn from the frozen whitelist."""
    allowed = {
        "schema_version",
        "mode",
        "outcome",
        "stage",
        "error_family",
        "batches_received",
        "messages_observed",
        "processing_performed",
        "final_commit_confirmed",
    }
    assert set(report) <= allowed
    assert report["schema_version"] == 1
    assert report["outcome"] in ("observed", "failed")
    assert report["stage"] in (
        "arguments",
        "configuration",
        "create",
        "consume",
        "complete",
    )
    assert report["error_family"] in (
        "none",
        "input",
        "configuration",
        "authentication",
        "permission",
        "api",
        "protocol",
        "transport",
        "interrupted",
        "unknown",
    )
    assert report.get("mode") in (None, "sync", "async")
    assert report["processing_performed"] is False
    assert report["final_commit_confirmed"] is False
    for key in ("batches_received", "messages_observed"):
        if key in report:
            assert isinstance(report[key], int) and report[key] >= 0


# ---------------------------------------------------------------------------
# run_sync / run_async against real clients on MockTransport.
# ---------------------------------------------------------------------------

_SIZES = (0, 1, 3)


def _run_helper(mode, harness, *, handler, max_batches, group=GROUP):
    """Invoke the matching run_* helper on a borrowed real client."""
    return _run(
        mode,
        harness,
        lambda client: ms.run_sync(client, group, handler=handler, max_batches=max_batches),
        lambda client: ms.run_async(client, group, handler=handler, max_batches=max_batches),
    )


def _empty_run(*, handler, max_batches, group=GROUP):
    """Run the local pre-validation path without any client or HTTP."""
    return _run_helper("sync", _Harness(), handler=handler, max_batches=max_batches, group=group)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize("max_batches", [1, 3, 10])
def test_run_happy_path_batches_and_wire(mode, max_batches):
    trace = []
    # Exactly ``max_batches`` consume responses are planned: every entry the
    # mock hands out must correspond to a real read, and no additional batch
    # is ever served beyond the declared budget.
    sizes = (list(_SIZES) + [2] * max_batches)[:max_batches]
    plan = iter(sizes)

    def business(request):
        if request.url.path in CREATE_PATHS:
            return httpx.Response(200, json=_create_body())
        assert request.url.path == CONSUME_PATH
        return httpx.Response(200, json=_consume_body([{"id": "m"}] * next(plan)))

    harness = _Harness(business=business, trace=trace)
    seen = []
    if mode == "sync":

        def handler(batch):
            seen.append(batch)
            trace.append(("handler",))
    else:

        async def handler(batch):
            seen.append(batch)
            await asyncio.sleep(0)  # a real suspension point inside the gate
            trace.append(("handler",))

    result = _run_helper(mode, harness, handler=handler, max_batches=max_batches)

    assert result.consumer_id == CONSUMER_ID
    assert result.batches_received == max_batches
    assert result.handlers_completed == max_batches
    assert result.messages_observed == sum(sizes)
    assert result.final_commit_confirmed is False

    # Strict ordering: create, then consume/handler pairs -- the handler
    # always completes (or is fully awaited) before the next read.
    expected = [("http", CREATE_PATH)]
    for _ in range(max_batches):
        expected += [("http", CONSUME_PATH), ("handler",)]
    assert trace == expected

    assert len(harness.auth_calls) == 1
    _check_create(harness.business_calls[0])
    for request in harness.business_calls[1:]:
        _check_consume(request)
    assert len(harness.business_calls) == 1 + max_batches
    # The handler received the exact ApiResponse objects the SDK returned.
    assert len(seen) == max_batches
    for batch in seen:
        assert isinstance(batch, ApiResponse)
        assert batch.http_status == 200
        assert batch.code == "200"
        assert isinstance(batch.data, list)
        assert batch.raw["rawExtra"] == "preserved"


def _handler_for(mode, sink=None):
    """Mode-matched real handler: a plain callable for sync, an awaitable
    producer for async -- the helper contract differs by mode."""
    if sink is None:
        sink = []
    if mode == "sync":
        return lambda batch: sink.append(batch)

    async def handler(batch):
        sink.append(batch)

    return handler


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_consumer_id_returned_verbatim_but_hidden_from_repr(mode):
    harness = _Harness()
    result = _run_helper(mode, harness, handler=_handler_for(mode), max_batches=1)
    assert result.consumer_id == CONSUMER_ID
    assert CONSUMER_ID not in repr(result)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_result_is_plain_frozen_data(mode):
    harness = _Harness()
    result = _run_helper(mode, harness, handler=_handler_for(mode), max_batches=1)
    assert type(result).__name__ == "MessageRunResult"
    assert type(result).__module__ == "examples.message_subscriptions"
    with pytest.raises(AttributeError):
        result.batches_received = 99
    for forbidden in ("raw", "data", "offset", "batch", "response"):
        assert not hasattr(result, forbidden)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "payloads,expected",
    [
        ([[]], [0]),
        ([[], []], [0, 0]),
        ([[{"id": "a"}, {"id": "b"}, {"id": "c"}]], [3]),
        ([[], [{"id": "a"}, {"id": "b"}]], [0, 2]),
    ],
    ids=["one-empty", "two-empty", "one-three", "mixed"],
)
def test_run_empty_batches_still_use_budget(mode, payloads, expected):
    trace = []
    sizes = iter(len(p) for p in payloads)

    def business(request):
        if request.url.path in CREATE_PATHS:
            return httpx.Response(200, json=_create_body())
        assert request.url.path == CONSUME_PATH
        return httpx.Response(200, json=_consume_body([{"id": "m"}] * next(sizes)))

    harness = _Harness(business=business, trace=trace)
    seen = []
    if mode == "sync":

        def handler(batch):
            seen.append(len(batch.data))
            trace.append(("handler",))
    else:

        async def handler(batch):
            seen.append(len(batch.data))
            trace.append(("handler",))

    result = _run_helper(mode, harness, handler=handler, max_batches=len(payloads))
    assert seen == expected
    assert result.messages_observed == sum(expected)
    assert result.batches_received == len(payloads)
    assert result.handlers_completed == len(payloads)
    # An empty list still consumed budget: exactly one consume per batch.
    assert [t for t, *_ in trace].count("http") == 1 + len(payloads)


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_handler_gets_same_response_object_with_unknown_fields(mode):
    bodies = iter(
        [
            _lapp({"consumerId": CONSUMER_ID, "weird": {"nested": [1]}}),
            _lapp([{"id": "m", "opaque": {"x": [1, 2]}}]),
        ]
    )
    harness = _Harness(business=lambda request: httpx.Response(200, json=next(bodies)))
    seen = []
    if mode == "sync":
        handler = seen.append
    else:

        async def handler(batch):
            seen.append(batch)

    _run_helper(mode, harness, handler=handler, max_batches=1)
    assert len(seen) == 1
    batch = seen[0]
    assert batch.data[0]["opaque"] == {"x": [1, 2]}
    assert batch.raw["rawExtra"] == "preserved"
    assert batch.code == "200"


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_group_passed_verbatim_into_path(mode):
    harness = _Harness()
    _run_helper(mode, harness, handler=_handler_for(mode), max_batches=1, group="group5")
    assert harness.business_calls[0].url.path == "/api/lapp/mq/v2/consumer/group5"
    _check_create(harness.business_calls[0], group="group5")


def test_run_borrowed_client_stays_open_across_runs():
    """The helper never closes or re-enters the caller-owned client."""
    harness = _Harness()
    client = harness.sync_client()
    first = ms.run_sync(client, GROUP, handler=lambda batch: None, max_batches=1)
    second = ms.run_sync(client, GROUP, handler=lambda batch: None, max_batches=1)
    assert first.consumer_id == second.consumer_id == CONSUMER_ID
    assert len(harness.business_calls) == 4
    assert len(harness.auth_calls) == 1  # still open: token cached, no re-auth
    client.close()


# ---------------------------------------------------------------------------
# Local contract validation: everything below must fail before any create.
# ---------------------------------------------------------------------------


def _no_http(harness):
    assert harness.factory_calls == []
    assert harness.auth_calls == []
    assert harness.business_calls == []


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "batches",
    [0, -1, 11, 100, True, False, 1.0, "3", None, b"2", object()],
    ids=[
        "zero",
        "neg",
        "eleven",
        "hundred",
        "true",
        "false",
        "float",
        "str",
        "none",
        "bytes",
        "object",
    ],
)
def test_run_rejects_bad_max_batches_before_any_http(mode, batches):
    harness = _Harness()
    with pytest.raises((TypeError, ValueError)):
        _run_helper(mode, harness, handler=lambda batch: None, max_batches=batches)
    _no_http(harness)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "group",
    ["group0", "group6", "GROUP1", " group1", "group1 ", "", "group", 0, 1, True, None, b"group1"],
)
def test_run_rejects_bad_group_before_any_http(mode, group):
    harness = _Harness()
    with pytest.raises((TypeError, ValueError)):
        _run_helper(mode, harness, handler=lambda batch: None, max_batches=1, group=group)
    _no_http(harness)


def _gen_fn(batch):
    yield batch


async def _async_gen_fn(batch):
    yield batch


class _GenCall:
    def __call__(self, batch):
        yield batch


class _AsyncGenCall:
    async def __call__(self, batch):
        yield batch


class _AsyncCall:
    async def __call__(self, batch):
        return None


async def _coro_handler(batch):
    return None


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "handler",
    [None, 42, "handler", object()],
    ids=["none", "int", "str", "object"],
)
def test_run_rejects_noncallable_handler_before_any_http(mode, handler):
    harness = _Harness()
    with pytest.raises(TypeError):
        _run_helper(mode, harness, handler=handler, max_batches=1)
    _no_http(harness)


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "handler",
    [_gen_fn, _async_gen_fn, _GenCall(), _AsyncGenCall()],
    ids=["gen", "asyncgen", "gen-call", "asyncgen-call"],
)
def test_run_rejects_generator_handlers_before_any_http(mode, handler):
    harness = _Harness()
    with pytest.raises(TypeError):
        _run_helper(mode, harness, handler=handler, max_batches=1)
    _no_http(harness)


@pytest.mark.parametrize(
    "handler",
    [_coro_handler, _AsyncCall()],
    ids=["coroutine-fn", "async-call"],
)
def test_run_sync_rejects_async_handler_before_any_http(handler):
    harness = _Harness()
    with pytest.raises(TypeError):
        ms.run_sync(harness.sync_client(), GROUP, handler=handler, max_batches=1)
    _no_http(harness)


@pytest.mark.parametrize(
    "produced",
    ["done", 42, {"k": 1}, (x for x in ())],
    ids=["str", "int", "dict", "gen-iter"],
)
def test_run_sync_non_none_return_stops_before_next_consume(produced):
    """A stray return value is a contract violation, not a completed batch."""
    trace = []
    harness = _Harness(trace=trace)
    client = harness.sync_client()
    seen = []

    def handler(batch):
        seen.append(batch)
        return produced

    with pytest.raises(TypeError):
        ms.run_sync(client, GROUP, handler=handler, max_batches=3)
    assert len(seen) == 1
    # create + one consume only; the offending handler ran once, no next read.
    assert len(harness.business_calls) == 2
    client.close()


def test_run_sync_awaitable_return_closes_and_stops():
    """A coroutine object returned in sync mode is closed, never awaited."""
    trace = []
    harness = _Harness(trace=trace)
    client = harness.sync_client()
    bodies = []

    async def inner():
        bodies.append("ran")  # never reached: the coroutine must not be awaited

    def handler(batch):
        return inner()

    with pytest.raises(TypeError):
        ms.run_sync(client, GROUP, handler=handler, max_batches=3)
    assert bodies == []
    assert len(harness.business_calls) == 2
    client.close()


def test_run_async_non_awaitable_return_stops_before_next_consume():
    """run_async requires handler(...) to produce an awaitable."""
    harness = _Harness()
    seen = []

    async def drive():
        client = harness.async_client()
        try:
            await ms.run_async(
                client, GROUP, handler=lambda batch: seen.append(batch), max_batches=3
            )
        finally:
            await client.aclose()

    with pytest.raises(TypeError):
        asyncio.run(drive())
    assert len(seen) == 1
    assert len(harness.business_calls) == 2


def test_run_async_awaitable_resolving_non_none_stops():
    """Awaitable contract: it must resolve to None, not a payload."""
    harness = _Harness()

    async def handler(batch):
        return "not-none"

    async def drive():
        client = harness.async_client()
        try:
            await ms.run_async(client, GROUP, handler=handler, max_batches=3)
        finally:
            await client.aclose()

    with pytest.raises(TypeError):
        asyncio.run(drive())
    assert len(harness.business_calls) == 2


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"consumerId": ""},
        {"consumerId": "   "},
        {"consumerId": None},
        {"consumerId": 123},
        {"consumerId": True},
        {"consumerId": "cid\ud800"},
        None,
        [],
        "text",
    ],
    ids=[
        "empty",
        "blank-id",
        "spaces-id",
        "none-id",
        "int-id",
        "bool-id",
        "surrogate-id",
        "null-data",
        "list-data",
        "str-data",
    ],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_bad_create_id_means_zero_consumes(mode, data):
    def business(request):
        assert request.url.path in CREATE_PATHS
        return httpx.Response(200, json=_create_body(data))

    harness = _Harness(business=business)
    with pytest.raises((TypeError, ValueError)):
        _run_helper(mode, harness, handler=_handler_for(mode), max_batches=3)
    # The consumer id is unverifiable: the helper must not guess or retry.
    assert [r.url.path for r in harness.business_calls] == [CREATE_PATH]


@pytest.mark.parametrize(
    "data",
    [{}, None, "text", 5, {"items": []}],
    ids=["dict", "null", "str", "int", "nested"],
)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_non_list_consume_data_stops_without_handler(mode, data):
    bodies = iter([_create_body(), _lapp(data)])
    harness = _Harness(business=lambda request: httpx.Response(200, json=next(bodies)))
    seen = []
    if mode == "sync":
        handler = seen.append
    else:

        async def handler(batch):
            seen.append(batch)

    with pytest.raises((TypeError, ValueError)):
        _run_helper(mode, harness, handler=handler, max_batches=3)
    assert seen == []  # handler never ran on the malformed batch
    assert len(harness.business_calls) == 2  # create + the single bad consume


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_sdk_error_propagates_same_object_without_followup(mode):
    marker = ApiError("marker", code="50001")

    def business(request):
        if request.url.path == CONSUME_PATH:
            raise marker
        assert request.url.path in CREATE_PATHS
        return httpx.Response(200, json=_create_body())

    harness = _Harness(business=business)
    seen = []
    if mode == "sync":
        handler = seen.append
    else:

        async def handler(batch):
            seen.append(batch)

    with pytest.raises(ApiError) as caught:
        _run_helper(mode, harness, handler=handler, max_batches=3)
    assert caught.value is marker
    assert seen == []
    # Exactly one create and the single failing consume; no retry, no recreate.
    assert [r.url.path for r in harness.business_calls] == [CREATE_PATH, CONSUME_PATH]


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_run_handler_error_is_identity_and_stops_reads(mode):
    marker = LookupError("handler sentinel")
    harness = _Harness()
    seen = []
    if mode == "sync":

        def handler(batch):
            seen.append(batch)
            raise marker
    else:

        async def handler(batch):
            seen.append(batch)
            raise marker

    with pytest.raises(LookupError) as caught:
        _run_helper(mode, harness, handler=handler, max_batches=5)
    assert caught.value is marker
    assert len(seen) == 1
    # The failing batch was read once; nothing was consumed after it.
    assert [r.url.path for r in harness.business_calls] == [CREATE_PATH, CONSUME_PATH]


class _ConsumeGate:
    """Lets create complete, then parks the first consume until cancelled."""

    def __init__(self):
        self.consume_started = asyncio.Event()
        self.cancelled_error: asyncio.CancelledError | None = None

    def respond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(200, json=_create_body())
        raise AssertionError("sync transport must not gate consume")

    async def arespond(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == CREATE_PATH:
            return httpx.Response(200, json=_create_body())
        self.consume_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            self.cancelled_error = error
            raise
        raise AssertionError("consume gate released unexpectedly")


def test_run_async_real_cancellation_identity_and_no_next_read():
    """Task cancellation inside the consume await propagates unchanged."""
    gate = _ConsumeGate()
    harness = _Harness()

    def factory(origin: str):
        harness.factory_calls.append(origin)
        return _GateAsyncTransport(gate, harness.business_calls)

    async def drive():
        client = AsyncClient(
            APP_KEY,
            APP_SECRET,
            region=RegionConfig.china(),
            clock=lambda: 1000.0,
            auth_transport=httpx.MockTransport(harness._handle_auth),
            transport_factory=factory,
        )
        inner = []

        async def invoke():
            try:

                async def handler(batch):
                    return None

                return await ms.run_async(client, GROUP, handler=handler, max_batches=3)
            except asyncio.CancelledError as error:
                inner.append(error)
                raise

        task = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(gate.consume_started.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.aclose()
        return task, inner

    task, inner = asyncio.run(drive())
    assert task.cancelled()
    assert len(inner) == 1
    assert gate.cancelled_error is not None
    # The CancelledError surfacing inside the SDK call is the same object
    # the transport observed; there is no next consume after cancellation.
    assert inner[0] is gate.cancelled_error
    # The gate parked the one consume that was sent: the wire shows exactly
    # one create and one consume, and nothing was read after the cancel.
    business_paths = [r.url.path for r in harness.business_calls]
    assert business_paths == [CREATE_PATH, CONSUME_PATH]


def test_run_async_cancellation_at_handler_await_is_identity():
    """Cancellation while awaiting the handler propagates, no next read."""
    started = asyncio.Event()
    harness = _Harness()
    seen = []
    observed = {}

    async def drive():
        client = harness.async_client()

        async def handler(batch):
            seen.append(batch)
            started.set()
            try:
                await asyncio.Event().wait()  # parked inside the handler await
            except asyncio.CancelledError as error:
                observed["handler"] = error
                raise
            return None

        async def invoke():
            try:
                return await ms.run_async(client, GROUP, handler=handler, max_batches=3)
            except asyncio.CancelledError as error:
                observed["task"] = error
                raise

        task = asyncio.create_task(invoke())
        try:
            await asyncio.wait_for(started.wait(), timeout=5.0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.aclose()
        return task

    task = asyncio.run(drive())
    assert task.cancelled()
    # The handler was entered once on the first batch; no further consume
    # was issued after the cancellation landed inside its await.
    assert len(seen) == 1
    # The CancelledError recorded inside the handler await is the very same
    # object that surfaced at the run_async await inside the cancelled task.
    assert observed["handler"] is observed["task"]
    business_paths = [r.url.path for r in harness.business_calls]
    assert business_paths == [CREATE_PATH, CONSUME_PATH]


class _GateAsyncTransport(httpx.AsyncBaseTransport):
    """Wraps the gate so the real AsyncClient awaits a parked consume."""

    def __init__(self, gate: _ConsumeGate, calls: list):
        self._gate = gate
        self._calls = calls

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._calls.append(request)
        return await self._gate.arespond(request)

    async def aclose(self) -> None:
        return None


def test_run_async_cancellation_before_consume_during_create():
    """Cancellation inside create still closes the client cleanly."""
    gate = _ConsumeGate()

    async def drive():
        client = AsyncClient(
            APP_KEY,
            APP_SECRET,
            region=RegionConfig.china(),
            clock=lambda: 1000.0,
            auth_transport=httpx.MockTransport(lambda r: httpx.Response(200, json=dict(AUTH_BODY))),
            transport_factory=lambda origin: _CreateGateTransport(gate),
        )
        task = asyncio.create_task(
            ms.run_async(client, GROUP, handler=lambda b: None, max_batches=2)
        )
        await asyncio.wait_for(gate.consume_started.wait(), timeout=5.0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await client.aclose()
        return task

    task = asyncio.run(drive())
    assert task.cancelled()


class _CreateGateTransport(httpx.AsyncBaseTransport):
    """Parks even the create request so cancellation lands mid-run."""

    def __init__(self, gate: _ConsumeGate):
        self._gate = gate

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._gate.consume_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as error:
            self._gate.cancelled_error = error
            raise
        raise AssertionError("create gate released unexpectedly")

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# main() CLI: real Client/AsyncClient behind MockTransport, env via monkeypatch.
# ---------------------------------------------------------------------------


def test_cli_sync_observes_once_with_real_client(monkeypatch, capsys):
    _set_env(monkeypatch)
    harness = _Harness()
    client = _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 0
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["mode"] == "sync"
    assert report["outcome"] == "observed"
    assert report["stage"] == "complete"
    assert report["error_family"] == "none"
    assert report["batches_received"] == 1
    assert report["messages_observed"] == len(BATCH_DATA)
    # Exactly auth + create + one consume; the client is then closed and the
    # consumer id is never echoed into the report.
    assert client.is_closed
    paths = [r.url.path for r in harness.business_calls]
    assert paths == [CREATE_PATH, CONSUME_PATH]
    _check_create(harness.business_calls[0])
    _check_consume(harness.business_calls[1])
    assert CONSUMER_ID not in json.dumps(report)


def test_cli_async_observes_once_with_real_client(monkeypatch, capsys):
    _set_env(monkeypatch)
    harness = _Harness()
    client = _install(monkeypatch, harness, "async")
    assert ms.main(["--mode", "async", "--execute"]) == 0
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["mode"] == "async"
    assert report["outcome"] == "observed"
    assert report["batches_received"] == 1
    assert report["messages_observed"] == len(BATCH_DATA)
    assert client.is_closed
    paths = [r.url.path for r in harness.business_calls]
    assert paths == [CREATE_PATH, CONSUME_PATH]


def test_cli_noop_handler_is_never_installed(monkeypatch, capsys):
    """The CLI performs its own private observe path; it must not smuggle a
    no-op handler through run_sync/run_async to claim one batch."""
    _set_env(monkeypatch)
    harness = _Harness()
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 0
    report = _read_report(capsys)
    assert report["processing_performed"] is False
    assert report["final_commit_confirmed"] is False


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_cli_group_env_selects_create_path(monkeypatch, capsys, mode):
    _set_env(monkeypatch, EZVIZ_MESSAGE_GROUP="group5")
    harness = _Harness()
    _install(monkeypatch, harness, mode)
    assert ms.main(["--mode", mode, "--execute"]) == 0
    report = _read_report(capsys)
    assert report["outcome"] == "observed"
    _check_create(harness.business_calls[0], group="group5")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "sync"],
        ["--mode", "async"],
        ["--mode", "bogus", "--execute"],
        ["--execute"],
        ["--mode", "sync", "--execute", "extra"],
        ["--mode", "sync", "--SENTINEL_flag", "--execute"],
    ],
    ids=[
        "empty",
        "sync-no-execute",
        "async-no-execute",
        "bad-mode",
        "no-mode",
        "trailing-arg",
        "unknown-flag",
    ],
)
def test_cli_argument_errors_exit_2_without_env_or_client(monkeypatch, capsys, argv):
    # No env is set on purpose: argument failure must precede any env read.
    constructed = []
    monkeypatch.setattr(ms, "Client", lambda *a, **kw: constructed.append(1))
    monkeypatch.setattr(ms, "AsyncClient", lambda *a, **kw: constructed.append(1))
    assert ms.main(argv) == 2
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["outcome"] == "failed"
    assert report["stage"] == "arguments"
    assert report["error_family"] == "input"
    assert constructed == []
    assert SENTINEL not in json.dumps(report)
    assert TOKEN not in json.dumps(report)


def test_cli_help_prints_and_exits_zero(monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        ms.main(["--help"])
    assert excinfo.value.code == 0
    out, err = capsys.readouterr()
    assert "python -m examples.message_subscriptions" in out
    assert "EZVIZ_" not in out


def test_cli_help_and_import_read_no_env_or_network(monkeypatch, capsys):
    real_get = os.environ.get

    def guarded_get(key, default=None):
        if str(key).startswith("EZVIZ_"):
            raise AssertionError("environment read during import/help")
        return real_get(key, default)

    def no_socket(*args, **kwargs):
        raise AssertionError("network access during import/help")

    monkeypatch.setattr(os.environ, "get", guarded_get)
    monkeypatch.setattr(socket, "socket", no_socket)
    importlib.reload(ms)  # importing the module must not read env or do I/O
    try:
        with pytest.raises(SystemExit) as excinfo:
            ms.main(["--help"])
        assert excinfo.value.code == 0
    finally:
        importlib.reload(ms)  # restore the normally-imported module
    out, err = capsys.readouterr()
    assert "python -m examples.message_subscriptions" in out
    assert "EZVIZ_" not in out


@pytest.mark.parametrize(
    "overrides",
    [
        {"EZVIZ_APP_KEY": None},
        {"EZVIZ_APP_SECRET": None},
        {"EZVIZ_REGION": None},
        {"EZVIZ_MESSAGE_GROUP": None},
        {"EZVIZ_MESSAGE_GROUP": "group0"},
        {"EZVIZ_MESSAGE_GROUP": "GROUP2"},
        {"EZVIZ_REGION": "overseas"},
        {"EZVIZ_AUTH_BASE_URL": "https://open.ezvizlife.com"},
    ],
    ids=[
        "no-key",
        "no-secret",
        "no-region",
        "no-group",
        "bad-group",
        "uppercase-group",
        "overseas",
        "auth-base-override",
    ],
)
def test_cli_configuration_errors_exit_2(monkeypatch, capsys, overrides):
    _set_env(monkeypatch, **overrides)
    constructed = []
    monkeypatch.setattr(ms, "Client", lambda *a, **kw: constructed.append(1))
    assert ms.main(["--mode", "sync", "--execute"]) == 2
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["outcome"] == "failed"
    assert report["stage"] == "configuration"
    assert report["error_family"] == "configuration"
    assert constructed == []


def test_cli_create_protocol_error_maps_stage_create(monkeypatch, capsys):
    _set_env(monkeypatch)

    def business(request):
        # The create response carries a non-dict data payload; the CLI must
        # attribute the failure to the create stage, not configuration.
        return httpx.Response(200, json=_create_body("not-a-dict"))

    harness = _Harness(business=business)
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 1
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["outcome"] == "failed"
    assert report["stage"] == "create"
    assert report["error_family"] == "protocol"


def test_cli_consume_protocol_error_maps_stage_consume(monkeypatch, capsys):
    _set_env(monkeypatch)
    bodies = iter([_create_body(), _lapp({"not": "a-list"})])
    harness = _Harness(business=lambda request: httpx.Response(200, json=next(bodies)))
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 1
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["outcome"] == "failed"
    assert report["stage"] == "consume"
    assert report["error_family"] == "protocol"


def test_cli_sdk_error_never_echoes_sentinels(monkeypatch, capsys):
    _set_env(monkeypatch)
    marker = ApiError(SENTINEL, code="50001")

    def business(request):
        if request.url.path == CONSUME_PATH:
            raise marker
        assert request.url.path in CREATE_PATHS
        return httpx.Response(200, json=_create_body())

    harness = _Harness(business=business)
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 1
    report = _read_report(capsys)
    _assert_fixed_shape(report)
    assert report["outcome"] == "failed"
    assert report["stage"] == "consume"
    assert report["error_family"] == "api"
    # The fixed report whitelist cannot leak the exception text, secrets or
    # the consumer id.
    for leaked in (SENTINEL, "50001", APP_SECRET, TOKEN, CONSUMER_ID):
        assert leaked not in json.dumps(report)


def test_cli_transport_error_maps_family_and_exit(monkeypatch, capsys):
    _set_env(monkeypatch)

    def business(request):
        if request.url.path == CONSUME_PATH:
            raise TransportError(SENTINEL, reason="network")
        assert request.url.path in CREATE_PATHS
        return httpx.Response(200, json=_create_body())

    harness = _Harness(business=business)
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 1
    report = _read_report(capsys)
    assert report["outcome"] == "failed"
    assert report["stage"] == "consume"
    assert report["error_family"] == "transport"
    assert SENTINEL not in json.dumps(report)


def test_cli_auth_failure_maps_authentication_family(monkeypatch, capsys):
    _set_env(monkeypatch)
    harness = _Harness(
        auth=lambda request: httpx.Response(
            200, json={"code": "10002", "msg": "bad token", "data": None}
        )
    )
    _install(monkeypatch, harness, "sync")
    assert ms.main(["--mode", "sync", "--execute"]) == 1
    report = _read_report(capsys)
    assert report["outcome"] == "failed"
    assert report["error_family"] == "authentication"
    # Authentication happens lazily inside the first SDK call: the failure
    # is attributed to the stage that triggered it (create).
    assert report["stage"] == "create"
