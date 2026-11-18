"""SP213 integration: client-mounted cloud_capture/message_subscriptions/hubs facades.

Exercises the real ``Client``/``AsyncClient`` attribute facades end-to-end
through ``httpx.MockTransport`` auth and business transports. Verifies the
six package exports, facade type/identity binding, zero I/O at
construction, per-operation wire path/token location, verbatim raw
``ApiResponse``, and the eight frozen diagnostic entrypoints emitting
``started``/``succeeded`` with the correct ``mode``/``region``.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from datetime import datetime

import httpx
import pytest

from ezviz_unified import (
    ApiResponse,
    AsyncClient,
    AsyncCloudCapture,
    AsyncHubs,
    AsyncMessageSubscriptions,
    Client,
    CloudCapture,
    DiagnosticRecorder,
    Hubs,
    MessageSubscriptions,
    RegionConfig,
)

APP_KEY = "INTEGRATION_APP_KEY"
APP_SECRET = "INTEGRATION_APP_SECRET"
TOKEN = "INTEGRATION_TEST_TOKEN"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}

DEVICE_SERIAL = "INTSERIAL123"
HUB_SERIAL = "HUBSERIAL1"
SUB_SERIAL = "SUBSERIAL12"
CONSUMER_ID = "INT_CONSUMER_ID"
TASK_ID = "INTTASK1"

INTERVAL_PATH = "/api/v3/open/cloud/video/frame/interval/start"
TIMING_PATH = "/api/v3/open/cloud/video/frame/timing/start"
TASK_PATH = "/api/v3/open/cloud/task/" + TASK_ID
CREATE_CONSUMER_PATH = "/api/lapp/mq/v2/consumer/group2"
CONSUME_PATH = "/api/lapp/mq/v2/consumer/messages"
LIST_SUBDEVICES_PATH = "/api/lapp/hub/device/sub/list"
SUBDEVICE_INFO_PATH = "/api/lapp/hub/device/sub/info"
CHANNELS_PATH = "/api/lapp/hub/device/camera/list"

_META_BODY = {
    "meta": {"code": 200, "message": "ok"},
    "data": {"taskId": TASK_ID},
    "sp213extra": "kept",
}
_LAPP_BODY = {
    "code": "200",
    "msg": "ok",
    "data": {"consumerId": CONSUMER_ID},
    "sp213extra": "kept",
}

INTERVAL_KWARGS = {
    "channel": 1,
    "rec_type": "cloud",
    "start_time": datetime(2024, 1, 2, 3, 4, 5),
    "end_time": datetime(2024, 1, 2, 4, 5, 6),
    "frame_interval": 2,
    "project_id": "INTPROJ_1",
}
TIMING_KWARGS = {
    "channel": 1,
    "rec_type": "local",
    "timing_points": [datetime(2024, 1, 2, 3, 4, 5)],
    "project_id": "INTPROJ_1",
}

ENTRYPOINTS = (
    "cloud_capture.create_interval_task",
    "cloud_capture.create_timing_task",
    "cloud_capture.task",
    "message_subscriptions.create_consumer",
    "message_subscriptions.consume",
    "hubs.list_subdevices",
    "hubs.subdevice_info",
    "hubs.channels",
)

SYNC_CALLS = {
    "cloud_capture.create_interval_task": lambda c: c.cloud_capture.create_interval_task(
        DEVICE_SERIAL, **INTERVAL_KWARGS
    ),
    "cloud_capture.create_timing_task": lambda c: c.cloud_capture.create_timing_task(
        DEVICE_SERIAL, **TIMING_KWARGS
    ),
    "cloud_capture.task": lambda c: c.cloud_capture.task(TASK_ID),
    "message_subscriptions.create_consumer": lambda c: c.message_subscriptions.create_consumer(
        "group2"
    ),
    "message_subscriptions.consume": lambda c: c.message_subscriptions.consume(
        CONSUMER_ID, offset=3
    ),
    "hubs.list_subdevices": lambda c: c.hubs.list_subdevices(HUB_SERIAL, version=""),
    "hubs.subdevice_info": lambda c: c.hubs.subdevice_info(SUB_SERIAL),
    "hubs.channels": lambda c: c.hubs.channels(SUB_SERIAL),
}
ASYNC_CALLS = {
    "cloud_capture.create_interval_task": lambda c: c.cloud_capture.create_interval_task(
        DEVICE_SERIAL, **INTERVAL_KWARGS
    ),
    "cloud_capture.create_timing_task": lambda c: c.cloud_capture.create_timing_task(
        DEVICE_SERIAL, **TIMING_KWARGS
    ),
    "cloud_capture.task": lambda c: c.cloud_capture.task(TASK_ID),
    "message_subscriptions.create_consumer": lambda c: c.message_subscriptions.create_consumer(
        "group2"
    ),
    "message_subscriptions.consume": lambda c: c.message_subscriptions.consume(
        CONSUMER_ID, offset=3
    ),
    "hubs.list_subdevices": lambda c: c.hubs.list_subdevices(HUB_SERIAL, version=""),
    "hubs.subdevice_info": lambda c: c.hubs.subdevice_info(SUB_SERIAL),
    "hubs.channels": lambda c: c.hubs.channels(SUB_SERIAL),
}

EXPECTED_PATH = {
    "cloud_capture.create_interval_task": INTERVAL_PATH,
    "cloud_capture.create_timing_task": TIMING_PATH,
    "cloud_capture.task": TASK_PATH,
    "message_subscriptions.create_consumer": CREATE_CONSUMER_PATH,
    "message_subscriptions.consume": CONSUME_PATH,
    "hubs.list_subdevices": LIST_SUBDEVICES_PATH,
    "hubs.subdevice_info": SUBDEVICE_INFO_PATH,
    "hubs.channels": CHANNELS_PATH,
}
EXPECTED_METHOD = {
    "cloud_capture.task": "GET",
    "message_subscriptions.create_consumer": "POST",
    "message_subscriptions.consume": "POST",
    "hubs.list_subdevices": "POST",
    "hubs.subdevice_info": "POST",
    "hubs.channels": "POST",
    "cloud_capture.create_interval_task": "POST",
    "cloud_capture.create_timing_task": "POST",
}
# Operations whose token travels in the query; all others use the form body.
QUERY_TOKEN = frozenset({"cloud_capture.task"})


class _Harness:
    """Synthetic auth and business transports wrapped around a real client."""

    def __init__(self, region="domestic"):
        self.region = region
        self.auth_calls = []
        self.business_calls = []
        self.factory_calls = []

    def _handle_auth(self, request):
        self.auth_calls.append(request)
        return httpx.Response(200, json=dict(AUTH_BODY))

    def _handle_business(self, request):
        self.business_calls.append(request)
        if request.url.path.startswith("/api/v3/open/"):
            return httpx.Response(200, json=dict(_META_BODY))
        return httpx.Response(200, json=dict(_LAPP_BODY))

    def _factory(self, origin):
        self.factory_calls.append(origin)
        return httpx.MockTransport(self._handle_business)

    def _kwargs(self, recorder=None):
        kwargs = {
            "app_key": APP_KEY,
            "app_secret": APP_SECRET,
            "region": RegionConfig.china()
            if self.region == "domestic"
            else RegionConfig.overseas(),
            "clock": lambda: 1000.0,
            "auth_transport": httpx.MockTransport(self._handle_auth),
            "transport_factory": self._factory,
        }
        if recorder is not None:
            kwargs["diagnostic_recorder"] = recorder
        return kwargs

    def sync_client(self, recorder=None):
        return Client(**self._kwargs(recorder))

    def async_client(self, recorder=None):
        return AsyncClient(**self._kwargs(recorder))


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _expected_raw(entrypoint):
    return dict(_META_BODY) if entrypoint.startswith("cloud_capture.") else dict(_LAPP_BODY)


def test_package_exports_six_classes():
    import ezviz_unified

    for name in (
        "CloudCapture",
        "AsyncCloudCapture",
        "MessageSubscriptions",
        "AsyncMessageSubscriptions",
        "Hubs",
        "AsyncHubs",
    ):
        assert name in ezviz_unified.__all__
        assert getattr(ezviz_unified, name) is globals()[name]


def test_sync_facade_types_and_identity_no_io():
    harness = _Harness()
    client = harness.sync_client()
    try:
        assert type(client.cloud_capture) is CloudCapture
        assert type(client.message_subscriptions) is MessageSubscriptions
        assert type(client.hubs) is Hubs
        # Facades are thin holders of the same client; construction does no I/O.
        assert client.cloud_capture._client is client
        assert client.message_subscriptions._client is client
        assert client.hubs._client is client
        assert harness.auth_calls == []
        assert harness.business_calls == []
        assert harness.factory_calls == []
    finally:
        client.close()


def test_async_facade_types_and_identity_no_io():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        try:
            assert type(client.cloud_capture) is AsyncCloudCapture
            assert type(client.message_subscriptions) is AsyncMessageSubscriptions
            assert type(client.hubs) is AsyncHubs
            assert client.cloud_capture._client is client
            assert client.message_subscriptions._client is client
            assert client.hubs._client is client
            assert harness.auth_calls == []
            assert harness.business_calls == []
            assert harness.factory_calls == []
        finally:
            await client.aclose()

    asyncio.run(main())


def _assert_wire(entrypoint, request):
    assert request.method == EXPECTED_METHOD[entrypoint]
    assert request.url.path == EXPECTED_PATH[entrypoint]
    if entrypoint in QUERY_TOKEN:
        assert request.url.params["accessToken"] == TOKEN
        assert b"accessToken" not in request.content
    else:
        assert "accessToken" not in request.url.params
        assert _form(request).get("accessToken") == [TOKEN]


def _assert_response(entrypoint, response):
    assert type(response) is ApiResponse
    assert response.raw == _expected_raw(entrypoint)
    assert response.raw["sp213extra"] == "kept"


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_sync_entrypoint_wire_raw_and_diagnostic(entrypoint):
    recorder = DiagnosticRecorder()
    harness = _Harness()
    client = harness.sync_client(recorder)
    try:
        response = SYNC_CALLS[entrypoint](client)
    finally:
        client.close()
    assert len(harness.auth_calls) == 1
    assert len(harness.business_calls) == 1
    _assert_wire(entrypoint, harness.business_calls[0])
    _assert_response(entrypoint, response)

    events = recorder.report()["events"]
    matched = [e for e in events if e["entrypoint"] == entrypoint and e["kind"] == "call"]
    started = [e for e in matched if e["outcome"] == "started"]
    succeeded = [e for e in matched if e["outcome"] == "succeeded"]
    assert len(started) == 1
    assert len(succeeded) == 1
    assert started[0]["span_id"] == succeeded[0]["span_id"]
    assert all(e["mode"] == "sync" for e in matched)
    assert all(e["region"] == "domestic" for e in matched)


@pytest.mark.parametrize("entrypoint", ENTRYPOINTS)
def test_async_entrypoint_wire_raw_and_diagnostic(entrypoint):
    async def main():
        recorder = DiagnosticRecorder()
        harness = _Harness()
        client = harness.async_client(recorder)
        try:
            response = await ASYNC_CALLS[entrypoint](client)
        finally:
            await client.aclose()
        assert len(harness.auth_calls) == 1
        assert len(harness.business_calls) == 1
        _assert_wire(entrypoint, harness.business_calls[0])
        _assert_response(entrypoint, response)

        events = recorder.report()["events"]
        matched = [e for e in events if e["entrypoint"] == entrypoint and e["kind"] == "call"]
        started = [e for e in matched if e["outcome"] == "started"]
        succeeded = [e for e in matched if e["outcome"] == "succeeded"]
        assert len(started) == 1
        assert len(succeeded) == 1
        assert started[0]["span_id"] == succeeded[0]["span_id"]
        assert all(e["mode"] == "async" for e in matched)
        assert all(e["region"] == "domestic" for e in matched)

    asyncio.run(main())


def test_sync_close_issues_no_extra_request():
    harness = _Harness()
    client = harness.sync_client()
    try:
        client.hubs.channels(SUB_SERIAL)
    finally:
        business_before = len(harness.business_calls)
        client.close()
    assert len(harness.business_calls) == business_before
    assert len(harness.business_calls) == 1


def test_async_close_issues_no_extra_request():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        try:
            await client.hubs.channels(SUB_SERIAL)
        finally:
            business_before = len(harness.business_calls)
            await client.aclose()
        assert len(harness.business_calls) == business_before
        assert len(harness.business_calls) == 1

    asyncio.run(main())
