"""Contract tests for the ten overseas Settings methods (sync and async).

Exercises real Client/AsyncClient instances over httpx.MockTransport via the
transport_factory hook. Operation metadata is captured by spying on the real
Client.request dispatcher; the spy still delegates, so the wire request is
performed. No module-level Operation constants are required.
"""

import asyncio
import urllib.parse

import httpx
import pytest

from ezviz_unified import AsyncClient, AuthenticationError, Client, RegionConfig, Token
from ezviz_unified.operations import Operation

BASE_URL = "https://isgpopen.ezvizlife.com"
HOST = "isgpopen.ezvizlife.com"
SN = "CS8009876"
OK = {"code": "200", "msg": "ok", "data": {"echo": True}}
TZ_LIST = "/api/lapp/timezone/list"


class Recorder:
    def __init__(self, bodies=None):
        self.requests = []
        self._bodies = list(bodies or [])

    def transport(self):
        return httpx.MockTransport(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        body = self._bodies.pop(0) if self._bodies else OK
        return httpx.Response(200, json=body)

    def form(self):
        raw = self.requests[-1].content.decode("utf-8")
        return dict(urllib.parse.parse_qsl(raw))


class SyncProvider:
    can_refresh = False

    def __init__(self):
        self.calls = 0
        self.invalidations = 0

    def get_token(self):
        self.calls += 1
        return Token(value="TEST_TOKEN", expires_at=2000, refresh_at=1900, api_base_url=BASE_URL)

    def invalidate(self, expected_value=None):
        self.invalidations += 1


class AsyncProvider(SyncProvider):
    async def get_token(self):
        self.calls += 1
        return Token(value="TEST_TOKEN", expires_at=2000, refresh_at=1900, api_base_url=BASE_URL)


def make_client(rec, provider=None):
    return Client(
        transport_factory=lambda origin: rec.transport(),
        token_provider=provider or SyncProvider(),
        clock=lambda: 1000,
        region=RegionConfig.overseas(api_base_url=BASE_URL),
    )


def make_async_client(rec, provider=None):
    return AsyncClient(
        transport_factory=lambda origin: rec.transport(),
        token_provider=provider or AsyncProvider(),
        clock=lambda: 1000,
        region=RegionConfig.overseas(api_base_url=BASE_URL),
    )


DECL = [
    ("get_pir_area", "/api/lapp/device/pir/get", True, "form"),
    ("set_pir_area", "/api/lapp/device/pir/set", False, "form"),
    ("get_chime", "/api/lapp/device/chime/get", True, "form"),
    ("set_chime", "/api/lapp/device/chime/set", False, "form"),
    ("get_infrared", "/api/lapp/device/infrared/switch/get", True, "form"),
    ("set_infrared", "/api/lapp/device/infrared/switch/set", False, "form"),
    ("list_timezones", TZ_LIST, True, "none"),
    ("set_timezone", "/api/lapp/device/timezone/set", False, "form"),
    ("get_language", "/api/lapp/device/language/get", True, "form"),
    ("set_language", "/api/lapp/device/language/set", False, "form"),
]

CALL_FOR = {
    "get_pir_area": lambda s: s.get_pir_area(SN),
    "set_pir_area": lambda s: s.set_pir_area(SN, "Zone-1,05"),
    "get_chime": lambda s: s.get_chime(SN),
    "set_chime": lambda s: s.set_chime(SN, 1, 5),
    "get_infrared": lambda s: s.get_infrared(SN),
    "set_infrared": lambda s: s.set_infrared(SN, True),
    "list_timezones": lambda s: s.list_timezones(),
    "set_timezone": lambda s: s.set_timezone(SN, "UTC", time_format=1),
    "get_language": lambda s: s.get_language(SN),
    "set_language": lambda s: s.set_language(SN, "ENGLISH"),
}


@pytest.mark.parametrize("name,path,read_only,token_location", DECL, ids=[row[0] for row in DECL])
def test_operation_metadata(name, path, read_only, token_location, monkeypatch):
    """Capture the Operation each facade call dispatches via Client.request.

    The spy wraps the real method and still delegates to it, so the wire
    request is performed exactly once. The patch is installed before the
    client is constructed so both lazy and eager binding are covered.
    """
    seen = []
    original = Client.request

    def spy(self, operation, *args, **kwargs):
        seen.append(operation)
        return original(self, operation, *args, **kwargs)

    monkeypatch.setattr(Client, "request", spy)
    rec, provider = Recorder(), SyncProvider()
    client = make_client(rec, provider)
    try:
        CALL_FOR[name](client.settings)
        assert len(seen) == 1
        assert len(rec.requests) == 1
        op = seen[0]
        assert isinstance(op, Operation)
        assert op.path == path
        assert op.read_only is read_only
        assert op.token_location == token_location
        assert provider.calls == (0 if token_location == "none" else 1)
        assert provider.invalidations == 0
    finally:
        client.close()


CASES = [
    {
        "id": "pir_get",
        "call": lambda s: s.get_pir_area(SN),
        "path": "/api/lapp/device/pir/get",
        "form": {"deviceSerial": SN},
    },
    {
        "id": "pir_get_ch0",
        "call": lambda s: s.get_pir_area(SN, channel=0),
        "path": "/api/lapp/device/pir/get",
        "form": {"deviceSerial": SN, "channelNo": "0"},
    },
    {
        "id": "pir_set",
        "call": lambda s: s.set_pir_area(SN, "Zone \u4e2d\u6587,05"),
        "path": "/api/lapp/device/pir/set",
        "form": {"deviceSerial": SN, "area": "Zone \u4e2d\u6587,05"},
    },
    {
        "id": "chime_get",
        "call": lambda s: s.get_chime(SN),
        "path": "/api/lapp/device/chime/get",
        "form": {"deviceSerial": SN},
    },
    {
        "id": "chime_get_neg",
        "call": lambda s: s.get_chime(SN, channel=-1),
        "path": "/api/lapp/device/chime/get",
        "form": {"deviceSerial": SN, "channelNo": "-1"},
    },
    {
        "id": "chime_set",
        "call": lambda s: s.set_chime(SN, 3, -30),
        "path": "/api/lapp/device/chime/set",
        "form": {"deviceSerial": SN, "type": "3", "duration": "-30"},
    },
    {
        "id": "ir_get",
        "call": lambda s: s.get_infrared(SN),
        "path": "/api/lapp/device/infrared/switch/get",
        "form": {"deviceSerial": SN},
    },
    {
        "id": "ir_on",
        "call": lambda s: s.set_infrared(SN, True),
        "path": "/api/lapp/device/infrared/switch/set",
        "form": {"deviceSerial": SN, "enable": "1"},
    },
    {
        "id": "ir_off",
        "call": lambda s: s.set_infrared(SN, False),
        "path": "/api/lapp/device/infrared/switch/set",
        "form": {"deviceSerial": SN, "enable": "0"},
    },
    {
        "id": "tz_list",
        "call": lambda s: s.list_timezones(),
        "path": TZ_LIST,
        "form": {},
        "public": True,
    },
    {
        "id": "tz_list_en",
        "call": lambda s: s.list_timezones(language="ENGLISH"),
        "path": TZ_LIST,
        "form": {"language": "ENGLISH"},
        "public": True,
    },
    {
        "id": "tz_list_cn",
        "call": lambda s: s.list_timezones(language="SIMPCN"),
        "path": TZ_LIST,
        "form": {"language": "SIMPCN"},
        "public": True,
    },
    {
        "id": "tz_set",
        "call": lambda s: s.set_timezone(SN, "Asia/Singapore", time_format=2),
        "path": "/api/lapp/device/timezone/set",
        "form": {"deviceSerial": SN, "timezone": "Asia/Singapore", "timeFormat": "2"},
    },
    {
        "id": "tz_dst1",
        "call": lambda s: s.set_timezone(SN, "Europe/Berlin", time_format=0, daylight_saving=True),
        "path": "/api/lapp/device/timezone/set",
        "form": {
            "deviceSerial": SN,
            "timezone": "Europe/Berlin",
            "timeFormat": "0",
            "daylightSaving": "1",
        },
    },
    {
        "id": "tz_dst0",
        "call": lambda s: s.set_timezone(SN, "UTC", time_format=1, daylight_saving=False),
        "path": "/api/lapp/device/timezone/set",
        "form": {"deviceSerial": SN, "timezone": "UTC", "timeFormat": "1", "daylightSaving": "0"},
    },
    {
        "id": "lang_get",
        "call": lambda s: s.get_language(SN),
        "path": "/api/lapp/device/language/get",
        "form": {"deviceSerial": SN},
    },
    {
        "id": "lang_set",
        "call": lambda s: s.set_language(SN, "GERMAN"),
        "path": "/api/lapp/device/language/set",
        "form": {"deviceSerial": SN, "language": "GERMAN"},
    },
]


def _assert_wire(case, rec, provider, resp):
    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req.method == "POST"
    assert (req.url.host, req.url.path) == (HOST, case["path"])
    expected = dict(case["form"])
    assert provider.invalidations == 0
    if case.get("public"):
        assert provider.calls == 0
        assert "accessToken" not in req.url.params
        assert not any("token" in k.lower() for k in req.headers)
    else:
        expected["accessToken"] = "TEST_TOKEN"
        assert provider.calls == 1
        ctype = req.headers["content-type"]
        assert ctype.startswith("application/x-www-form-urlencoded")
    assert rec.form() == expected
    assert resp.http_status == 200
    assert resp.code == "200"
    assert resp.data == OK["data"]
    assert not hasattr(resp, "status_code")
    assert not hasattr(resp, "msg")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_sync_wire(case):
    rec, provider = Recorder(), SyncProvider()
    client = make_client(rec, provider)
    try:
        resp = case["call"](client.settings)
    finally:
        client.close()
    _assert_wire(case, rec, provider, resp)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_async_wire(case):
    rec, provider = Recorder(), AsyncProvider()

    async def main():
        client = make_async_client(rec, provider)
        try:
            return await case["call"](client.settings)
        finally:
            await client.aclose()

    _assert_wire(case, rec, provider, asyncio.run(main()))


INVALID = [
    ("empty_serial", lambda s: s.get_pir_area(""), ValueError),
    ("serial_int", lambda s: s.get_infrared(123), ValueError),
    ("bool_channel", lambda s: s.get_pir_area(SN, channel=True), ValueError),
    ("str_channel", lambda s: s.get_chime(SN, channel="1"), ValueError),
    ("empty_area", lambda s: s.set_pir_area(SN, ""), ValueError),
    ("int_area", lambda s: s.set_pir_area(SN, 7), ValueError),
    ("chime_type_0", lambda s: s.set_chime(SN, 0, 5), ValueError),
    ("chime_type_4", lambda s: s.set_chime(SN, 4, 5), ValueError),
    ("chime_type_bool", lambda s: s.set_chime(SN, True, 5), ValueError),
    ("chime_type_str", lambda s: s.set_chime(SN, "2", 5), ValueError),
    ("duration_str", lambda s: s.set_chime(SN, 2, "5"), ValueError),
    ("duration_bool", lambda s: s.set_chime(SN, 2, False), ValueError),
    ("infrared_int", lambda s: s.set_infrared(SN, 1), ValueError),
    ("infrared_none", lambda s: s.set_infrared(SN, None), ValueError),
    ("tzlist_lang", lambda s: s.list_timezones(language="GERMAN"), ValueError),
    ("tzlist_lang_int", lambda s: s.list_timezones(language=1), ValueError),
    ("empty_timezone", lambda s: s.set_timezone(SN, "", time_format=1), ValueError),
    ("time_format_3", lambda s: s.set_timezone(SN, "UTC", time_format=3), ValueError),
    ("time_format_bool", lambda s: s.set_timezone(SN, "UTC", time_format=True), ValueError),
    ("time_format_missing", lambda s: s.set_timezone(SN, "UTC"), TypeError),
    ("time_format_positional", lambda s: s.set_timezone(SN, "UTC", 1), TypeError),
    ("dst_int", lambda s: s.set_timezone(SN, "UTC", time_format=1, daylight_saving=1), ValueError),
    ("empty_lang", lambda s: s.set_language(SN, ""), ValueError),
    ("int_lang", lambda s: s.set_language(SN, 5), ValueError),
]


@pytest.mark.parametrize("call,exc", [(c[1], c[2]) for c in INVALID], ids=[c[0] for c in INVALID])
def test_invalid_rejected_without_io(call, exc):
    rec, provider = Recorder(), SyncProvider()
    client = make_client(rec, provider)
    try:
        with pytest.raises(exc):
            call(client.settings)
        assert rec.requests == []
        assert provider.calls == 0
        assert provider.invalidations == 0
    finally:
        client.close()


def test_non_utf8_serial_rejected_cleanly():
    rec, provider = Recorder(), SyncProvider()
    client = make_client(rec, provider)
    try:
        with pytest.raises(ValueError) as excinfo:
            client.settings.get_pir_area("bad" + chr(0xD800) + "serial")
        err = excinfo.value
        for attr in ("input", "cause", "context"):
            assert not hasattr(err, attr)
        assert err.__cause__ is None
        assert err.__context__ is None
        assert rec.requests == []
        assert provider.calls == 0
        assert provider.invalidations == 0
    finally:
        client.close()


def test_china_region_rejected_without_io():
    rec, provider = Recorder(), SyncProvider()
    client = Client(
        transport_factory=lambda origin: rec.transport(),
        token_provider=provider,
        clock=lambda: 1000,
        region=RegionConfig.china(),
    )
    try:
        for call in (lambda s: s.get_pir_area(SN), lambda s: s.list_timezones()):
            with pytest.raises(ValueError):
                call(client.settings)
        assert rec.requests == []
        assert provider.calls == 0
        assert provider.invalidations == 0
    finally:
        client.close()


def test_10002_not_retried():
    pairs = ((lambda s: s.set_infrared(SN, True), False), (lambda s: s.list_timezones(), True))
    for call, public in pairs:
        bodies = [{"code": "10002", "msg": "bad token"}, OK]
        rec, provider = Recorder(bodies), SyncProvider()
        client = make_client(rec, provider)
        try:
            with pytest.raises(AuthenticationError) as excinfo:
                call(client.settings)
            assert len(rec.requests) == 1
            assert excinfo.value.code == "10002"
            if public:
                assert provider.calls == 0
                assert provider.invalidations == 0
            else:
                assert provider.calls == 1
        finally:
            client.close()


def test_data_field_semantics():
    missing = {"code": "200", "msg": "ok"}
    rich = {
        "code": "200",
        "msg": "ok",
        "data": {"list": [{"name": "\u65b0\u52a0\u5761", "offset": 8}], "n": 1},
    }
    client = make_client(Recorder([missing, rich]))
    try:
        resp = client.settings.get_language(SN)
        assert (resp.http_status, resp.code, resp.data) == (200, "200", None)
        resp = client.settings.list_timezones()
        assert resp.data == rich["data"]
        assert resp.raw == rich
    finally:
        client.close()


def test_anonymous_mode():
    rec = Recorder()
    anon = Client(
        anonymous=True,
        transport_factory=lambda origin: rec.transport(),
        region=RegionConfig.overseas(api_base_url=BASE_URL),
    )
    try:
        resp = anon.settings.list_timezones(language="ENGLISH")
        assert resp.code == "200"
        assert rec.form() == {"language": "ENGLISH"}
        assert "accessToken" not in rec.form()
        with pytest.raises(ValueError):
            anon.settings.get_pir_area(SN)
        assert len(rec.requests) == 1
    finally:
        anon.close()
    with pytest.raises(ValueError):
        Client(
            anonymous=True,
            transport_factory=lambda origin: rec.transport(),
            token_provider=SyncProvider(),
            region=RegionConfig.overseas(api_base_url=BASE_URL),
        )
