"""Offline wire and validation tests for the domestic HUB facades.

``Hubs`` and ``AsyncHubs`` are exercised directly against real
``Client``/``AsyncClient`` instances whose transports are
``httpx.MockTransport`` objects with fully synthetic authentication.
Nothing reaches the network and no pytest-asyncio plugin is required.
"""

from __future__ import annotations

import asyncio
import urllib.parse

import httpx
import pytest

from ezviz_unified import ApiError, AsyncClient, Client, RegionConfig
from ezviz_unified.hubs import AsyncHubs, Hubs

APP_KEY = "TEST_APP_KEY"
APP_SECRET = "TEST_APP_SECRET"
TOKEN = "TEST_TOKEN"
HUB_SERIAL = "HUB1234567"
SUB_SERIAL = "SUB7654321"

LIST_PATH = "/api/lapp/hub/device/sub/list"
INFO_PATH = "/api/lapp/hub/device/sub/info"
CHANNELS_PATH = "/api/lapp/hub/device/camera/list"

AUTH_BODY = {"code": "200", "data": {"accessToken": TOKEN, "expireTime": 2000000}}
SUCCESS_BODY = {
    "code": "200",
    "msg": "ok",
    "data": {"items": [{"deviceSerial": SUB_SERIAL, "status": 1}]},
    "extraField": "kept",
}

OPS = ("list_subdevices", "subdevice_info", "channels")
BAD_SERIALS = (None, b"HUB123", "", "   ", "hub1234", "HUB\ud800")
BAD_VERSIONS = (123, 1.5, True, b"v1", "v\ud800")
VERBATIM_SERIALS = ("HUB７７７", "HUB 12", "HUB-9_X", "9")
VERBATIM_VERSIONS = ("", "   ", "v2.0-beta", "版本v1_é")

_MISSING = object()


def _region(label: str) -> RegionConfig:
    """Return the real RegionConfig for ``label`` ("domestic"/"overseas")."""
    if label == "domestic":
        return RegionConfig.china()
    return RegionConfig.overseas()


class _Harness:
    """Synthetic auth and business transports wrapped around a real client."""

    def __init__(self, *, region="domestic", responder=None):
        self.region = region
        self.responder = responder or (lambda request: httpx.Response(200, json=dict(SUCCESS_BODY)))
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


def _form(request):
    return urllib.parse.parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _assert_wire(request, path, serial, *, version=_MISSING):
    assert request.method == "POST"
    assert request.url.path == path
    assert not request.url.query
    content_type = request.headers.get("content-type", "")
    assert "application/x-www-form-urlencoded" in content_type
    form = _form(request)
    assert form.get("accessToken") == [TOKEN]
    assert form.get("deviceSerial") == [serial]
    if version is _MISSING:
        assert "version" not in form
    else:
        assert form.get("version") == [version]


def _assert_response(response):
    assert response.http_status == 200
    assert response.code == "200"
    assert response.message == "ok"
    assert response.data == SUCCESS_BODY["data"]
    # ``raw`` is the original response dict, preserved verbatim.
    assert response.raw == SUCCESS_BODY
    assert response.raw["extraField"] == "kept"


def _assert_no_io(harness):
    assert harness.factory_calls == []
    assert harness.auth_calls == []
    assert harness.business_calls == []


def test_sync_list_subdevices_wire():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        response = hubs.list_subdevices(HUB_SERIAL, version="v2.0")
    finally:
        client.close()
    assert len(harness.auth_calls) == 1
    assert len(harness.factory_calls) == 1
    assert len(harness.business_calls) == 1
    _assert_wire(harness.business_calls[0], LIST_PATH, HUB_SERIAL, version="v2.0")
    _assert_response(response)


def test_sync_subdevice_info_wire():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        response = hubs.subdevice_info(SUB_SERIAL)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    _assert_wire(harness.business_calls[0], INFO_PATH, SUB_SERIAL)
    _assert_response(response)


def test_sync_channels_wire():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        response = hubs.channels(SUB_SERIAL)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    _assert_wire(harness.business_calls[0], CHANNELS_PATH, SUB_SERIAL)
    _assert_response(response)


def test_sync_parent_and_child_serials_are_not_swapped():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        hubs.list_subdevices(HUB_SERIAL)
        hubs.subdevice_info(SUB_SERIAL)
        hubs.channels(SUB_SERIAL)
    finally:
        client.close()
    calls = harness.business_calls
    assert [call.url.path for call in calls] == [LIST_PATH, INFO_PATH, CHANNELS_PATH]
    serials = [_form(call).get("deviceSerial") for call in calls]
    assert serials == [[HUB_SERIAL], [SUB_SERIAL], [SUB_SERIAL]]
    assert len(harness.auth_calls) == 1
    assert len(harness.factory_calls) >= 1


def test_sync_version_omitted_when_none():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        hubs.list_subdevices(HUB_SERIAL, version=None)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    form = _form(harness.business_calls[0])
    assert "version" not in form


@pytest.mark.parametrize("version", VERBATIM_VERSIONS)
def test_sync_version_sent_verbatim(version):
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        hubs.list_subdevices(HUB_SERIAL, version=version)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    form = _form(harness.business_calls[0])
    assert form.get("version") == [version]


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("bad", BAD_SERIALS)
def test_sync_invalid_serial_rejected_before_io(op, bad):
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError):
            getattr(hubs, op)(bad)
    finally:
        client.close()
    _assert_no_io(harness)


@pytest.mark.parametrize("bad", BAD_VERSIONS)
def test_sync_invalid_version_rejected_before_io(bad):
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError):
            hubs.list_subdevices(HUB_SERIAL, version=bad)
    finally:
        client.close()
    _assert_no_io(harness)


def test_sync_serial_error_does_not_echo_value():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError) as excinfo:
            hubs.channels("secretlowercase99")
    finally:
        client.close()
    assert "secretlowercase99" not in str(excinfo.value)
    _assert_no_io(harness)


def test_sync_version_error_does_not_echo_value():
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError) as excinfo:
            hubs.list_subdevices(HUB_SERIAL, version="v_secret_\ud800")
    finally:
        client.close()
    assert "v_secret_" not in str(excinfo.value)
    _assert_no_io(harness)


@pytest.mark.parametrize("op", OPS)
def test_sync_overseas_region_rejected(op):
    harness = _Harness(region="overseas")
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError) as excinfo:
            getattr(hubs, op)(SUB_SERIAL)
    finally:
        client.close()
    assert "domestic" in str(excinfo.value) or "region" in str(excinfo.value)
    _assert_no_io(harness)


def test_sync_region_rejected_before_parameter_validation():
    harness = _Harness(region="overseas")
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ValueError) as excinfo:
            hubs.list_subdevices("lowercasebad", version=123)
    finally:
        client.close()
    assert "domestic" in str(excinfo.value) or "region" in str(excinfo.value)
    _assert_no_io(harness)


def test_sync_int_code_is_not_treated_as_success():
    harness = _Harness(
        responder=lambda request: httpx.Response(200, json={"code": 200, "msg": "ok", "data": {}})
    )
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ApiError) as excinfo:
            hubs.channels(SUB_SERIAL)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    # The String-200 policy rejects an int 200 code as a business failure.
    assert excinfo.value.code == 200
    assert excinfo.value.http_status == 200


def test_sync_business_error_raises_api_error():
    harness = _Harness(
        responder=lambda request: httpx.Response(
            200, json={"code": "50001", "msg": "denied", "data": None}
        )
    )
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ApiError) as excinfo:
            hubs.subdevice_info(SUB_SERIAL)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    assert excinfo.value.code == "50001"
    assert excinfo.value.http_status == 200


def test_sync_http_error_raises_api_error():
    harness = _Harness(responder=lambda request: httpx.Response(503, text="upstream down"))
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        with pytest.raises(ApiError) as excinfo:
            hubs.list_subdevices(HUB_SERIAL)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    assert excinfo.value.http_status == 503


@pytest.mark.parametrize("serial", VERBATIM_SERIALS)
def test_sync_serial_sent_verbatim(serial):
    harness = _Harness()
    client = harness.sync_client()
    hubs = Hubs(client)
    try:
        hubs.channels(serial)
    finally:
        client.close()
    assert len(harness.business_calls) == 1
    form = _form(harness.business_calls[0])
    assert form.get("deviceSerial") == [serial]


def test_async_list_subdevices_wire():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            response = await hubs.list_subdevices(HUB_SERIAL, version="v1")
        finally:
            await client.aclose()
        assert len(harness.business_calls) == 1
        _assert_wire(harness.business_calls[0], LIST_PATH, HUB_SERIAL, version="v1")
        _assert_response(response)

    asyncio.run(main())


def test_async_subdevice_info_wire():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            response = await hubs.subdevice_info(SUB_SERIAL)
        finally:
            await client.aclose()
        assert len(harness.business_calls) == 1
        _assert_wire(harness.business_calls[0], INFO_PATH, SUB_SERIAL)
        _assert_response(response)

    asyncio.run(main())


def test_async_channels_wire():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            response = await hubs.channels(SUB_SERIAL)
        finally:
            await client.aclose()
        assert len(harness.business_calls) == 1
        _assert_wire(harness.business_calls[0], CHANNELS_PATH, SUB_SERIAL)
        _assert_response(response)

    asyncio.run(main())


@pytest.mark.parametrize("op", OPS)
@pytest.mark.parametrize("bad", (None, "", "subabc"))
def test_async_invalid_serial_rejected_before_io(op, bad):
    async def main():
        harness = _Harness()
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            with pytest.raises(ValueError):
                await getattr(hubs, op)(bad)
        finally:
            await client.aclose()
        _assert_no_io(harness)

    asyncio.run(main())


@pytest.mark.parametrize("op", OPS)
def test_async_overseas_region_rejected(op):
    async def main():
        harness = _Harness(region="overseas")
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            with pytest.raises(ValueError) as excinfo:
                await getattr(hubs, op)(SUB_SERIAL)
        finally:
            await client.aclose()
        assert "domestic" in str(excinfo.value) or "region" in str(excinfo.value)
        _assert_no_io(harness)

    asyncio.run(main())


def test_async_version_empty_preserved():
    async def main():
        harness = _Harness()
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            await hubs.list_subdevices(HUB_SERIAL, version="")
        finally:
            await client.aclose()
        assert len(harness.business_calls) == 1
        form = _form(harness.business_calls[0])
        assert form.get("version") == [""]

    asyncio.run(main())


def test_async_business_error_raises_api_error():
    async def main():
        harness = _Harness(
            responder=lambda request: httpx.Response(
                200, json={"code": "50001", "msg": "denied", "data": None}
            )
        )
        client = harness.async_client()
        hubs = AsyncHubs(client)
        try:
            with pytest.raises(ApiError) as excinfo:
                await hubs.channels(SUB_SERIAL)
        finally:
            await client.aclose()
        assert len(harness.business_calls) == 1
        assert excinfo.value.code == "50001"
        assert excinfo.value.http_status == 200

    asyncio.run(main())
