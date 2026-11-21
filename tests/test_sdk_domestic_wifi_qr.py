"""Offline coverage of domestic Wi-Fi QR generation with synthetic input and non-image data."""

import asyncio
from urllib.parse import parse_qsl

import httpx
import pytest
from test_sdk_device_utc import TOKEN, _client

from ezviz_unified import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    RegionConfig,
    TransportError,
)

OP = "generate_wifi_qr"
PATH = "/api/lapp/device/wifi/qrcode"
SSID = " 零 +%&=/ SSID_PRIVATE "
PASSWORD = " 密 +%&=/ PASSWORD_PRIVATE "
IMAGE = " not an image: IMAGE_PRIVATE! "
MSG = "UPSTREAM_PRIVATE"
MISS = object()
GOOD = {"code": "200", "msg": MSG, "data": {"imageData": IMAGE, "extension": ["EXT_PRIVATE", None]}}


@pytest.fixture(autouse=True)
def domestic_token_reply(monkeypatch):
    def auth(request):
        assert request.url.host == "open.ys7.com"
        return httpx.Response(
            200, json={"code": "200", "data": {"accessToken": TOKEN, "expireTime": 9999999999999}}
        )

    monkeypatch.setattr("test_sdk_device_utc._auth_ok", auth)


@pytest.fixture(params=("sync", "async"))
def mode(request):
    return request.param


def invoke(mode, *steps, ssid=SSID, password=PASSWORD, region=None, args=None, kwargs=None):
    positional = (ssid,) if args is None else args
    options = {"password": password} if kwargs is None else kwargs
    replies = iter(steps)

    def business(req):
        step = next(replies)
        if isinstance(step, BaseException):
            raise step
        return step if isinstance(step, httpx.Response) else httpx.Response(200, json=step)

    client, rec, seen = _client(mode, biz=business, region=region or RegionConfig.china())

    def run():
        if mode == "sync":
            with client:
                return client.devices.generate_wifi_qr(*positional, **options)

        async def execute():
            async with client:
                return await client.devices.generate_wifi_qr(*positional, **options)

        return asyncio.run(execute())

    return run, rec, seen


def outcomes(rec, mode, kind):
    key = "entrypoint" if kind == "call" else "operation"
    return [
        e["outcome"]
        for e in rec.report()["events"]
        if e["kind"] == kind and e["mode"] == mode and e[key] == "devices.generate_wifi_qr"
    ]


def assert_wire(req, ssid, password):
    assert (
        req.method == "POST"
        and req.url.scheme == "https"
        and req.url.host == "open.ys7.com"
        and req.url.path == PATH
        and req.url.query == b""
    )
    assert req.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert set(req.headers).isdisjoint(
        {"accesstoken", "authorization", "ssid", "password", "deviceserial", "channelno"}
    )
    assert sorted(parse_qsl(req.content.decode("utf-8"), keep_blank_values=True)) == sorted(
        [("accessToken", TOKEN), ("ssid", ssid), ("password", password)]
    )


@pytest.mark.parametrize(
    "ssid,password",
    [(SSID, PASSWORD), ("   ", "  "), (" ", "MiXeD"), ("空 格", "     "), (SSID, "x" * 2048)],
)
@pytest.mark.parametrize("image_data", ["", IMAGE])
def test_generate_wire(mode, ssid, password, image_data):
    data = {**GOOD["data"], "imageData": image_data}
    body = {**GOOD, "data": data, "extra": [0, None], "result": {"code": "10002"}}
    run, r, seen = invoke(mode, body, ssid=ssid, password=password)
    answer = run()
    assert (
        answer.raw == body
        and answer.data == data
        and answer.code == "200"
        and answer.message == "***"
    )
    (auth,), (biz,) = seen["auth"], seen["biz"]
    assert (
        auth.url.host == "open.ys7.com"
        and auth.url.scheme == "https"
        and auth.method == "POST"
        and auth.url.path == "/api/lapp/token/get"
    )
    assert_wire(biz, ssid, password)
    assert outcomes(r, mode, "call") == ["started", "succeeded"] and outcomes(
        r, mode, "attempt"
    ) == ["started", "succeeded"]


def test_overseas_rejected_before_validation(mode):
    problems = []
    for ssid, password in ((SSID, PASSWORD), (object(), object())):
        run, r, seen = invoke(
            mode,
            ssid=ssid,
            password=password,
            region=RegionConfig.overseas(auth_base_url="https://synthetic-auth.ezvizlife.com"),
        )
        with pytest.raises(ValueError) as exc:
            run()
        problems.append(str(exc.value))
        assert seen == {"auth": [], "biz": []} and not outcomes(r, mode, "attempt")
        assert outcomes(r, mode, "call") == ["started", "failed"]
    assert problems[0] == problems[1]


@pytest.mark.parametrize("field", ["ssid", "password"])
@pytest.mark.parametrize(
    "invalid",
    [None, True, 3, b"BYTES_PRIVATE", "", "\ud800SURROGATE_PRIVATE", "SURROGATE_PRIVATE\udfff"],
)
def test_bad_text_before_auth(mode, field, invalid):
    run, r, seen = invoke(mode, **{field: invalid})
    with pytest.raises(ValueError) as exc:
        run()
    err = exc.value
    details = (
        str(err),
        repr(err),
        repr(err.args),
        repr(err.__cause__),
        repr(err.__context__),
        repr(r.report()),
    )
    for marker in ("BYTES_PRIVATE", "SURROGATE_PRIVATE", "SSID_PRIVATE", "PASSWORD_PRIVATE"):
        assert all(marker not in text for text in details)
    assert err.__cause__ is None
    assert not isinstance(err.__context__, UnicodeEncodeError)
    assert seen == {"auth": [], "biz": []} and not outcomes(r, mode, "attempt")


@pytest.mark.parametrize(
    "args,kw",
    [
        ((SSID,), {}),
        ((SSID, PASSWORD), {}),
        ((SSID,), {"password": PASSWORD, "device_serial": "SERIAL"}),
        ((SSID,), {"password": PASSWORD, "channel_no": "1"}),
        ((), {"password": PASSWORD}),
    ],
)
def test_public_signature(mode, args, kw):
    run, _, seen = invoke(mode, args=args, kwargs=kw)
    with pytest.raises(TypeError):
        run()
    assert seen == {"auth": [], "biz": []}


BAD_DATA = [
    MISS,
    None,
    {},
    [],
    "placeholder",
    {"data": {"imageData": "placeholder"}},
    {"other": "placeholder"},
    {"imageData": False},
    {"imageData": 1},
    {"imageData": 1.0},
    {"imageData": None},
    {"imageData": []},
    {"imageData": {}},
]


@pytest.mark.parametrize("data", BAD_DATA)
def test_invalid_data(mode, data):
    reply = {"code": "200", "msg": MSG}
    if data is not MISS:
        reply["data"] = data
    run, rec, seen = invoke(mode, reply)
    with pytest.raises(ProtocolError):
        run()
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and not outcomes(rec, mode, "retry")


@pytest.mark.parametrize(
    "reply,expected",
    [
        ({"result": {"code": "200"}}, ProtocolError),
        ({"body": {"meta": {"code": 200}}}, ProtocolError),
        ({"code": True}, ProtocolError),
        ({"code": 200.0}, ProtocolError),
        ({"code": 200}, ApiError),
    ],
)
def test_wrong_envelope(mode, reply, expected):
    run, _, seen = invoke(mode, {**reply, "data": {"imageData": IMAGE}})
    with pytest.raises(expected) as exc:
        run()
    assert type(exc.value) is expected and len(seen["auth"]) == len(seen["biz"]) == 1


@pytest.mark.parametrize("code", [10002, "10002"])
def test_expired_token_never_replays(mode, code):
    run, r, seen = invoke(mode, {"code": code, "msg": MSG}, GOOD)
    with pytest.raises(AuthenticationError) as exc:
        run()
    assert exc.value.code == code and type(exc.value.code) is type(code)
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    assert_wire(seen["biz"][0], SSID, PASSWORD)
    assert outcomes(r, mode, "retry") == [] and outcomes(r, mode, "attempt") == [
        "started",
        "failed",
    ]


VISIBLE = (
    "10001",
    "10005",
    "10017",
    "49999",
    "400",
    "403",
    "404",
    "500",
    "10031",
)


@pytest.mark.parametrize("code", [typed(x) for x in VISIBLE for typed in (str, int)] + [200])
def test_documented_errors(mode, code):
    run, rec, seen = invoke(mode, {"code": code, "msg": MSG})
    expected = PermissionDeniedError if str(code) == "10031" else ApiError
    with pytest.raises(expected) as exc:
        run()
    assert (
        type(exc.value) is expected
        and exc.value.code == code
        and type(exc.value.code) is type(code)
    )
    assert (
        exc.value.message == "***"
        and len(seen["auth"]) == len(seen["biz"]) == 1
        and not outcomes(rec, mode, "retry")
    )


@pytest.mark.parametrize(
    "http_status,code,expected",
    [
        (200, "UNTRUSTED_CODE", ApiError),
        (200, 93174625, ApiError),
        (403, "UNTRUSTED_CODE", PermissionDeniedError),
        (503, MISS, ApiError),
    ],
)
def test_private_errors(mode, http_status, code, expected, caplog):
    body = {"msg": MSG + SSID + PASSWORD + IMAGE, "data": {"imageData": IMAGE}}
    if code is not MISS:
        body["code"] = code
    run, rec, seen = invoke(
        mode,
        httpx.Response(
            http_status,
            json=body,
            extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
        ),
    )
    with caplog.at_level("INFO", logger="httpx"), pytest.raises(expected) as exc:
        run()
    assert (
        type(exc.value) is expected
        and exc.value.http_status == http_status
        and exc.value.message == "***"
    )
    assert exc.value.code == (None if code is MISS else "***")
    assert len(seen["auth"]) == len(seen["biz"]) == 1
    visible = " ".join((str(exc.value), repr(exc.value), repr(rec.report()), caplog.text))
    for marker in (
        "SSID_PRIVATE",
        "PASSWORD_PRIVATE",
        "IMAGE_PRIVATE",
        MSG,
        TOKEN,
        "VERSION_PRIVATE",
        "REASON_PRIVATE",
        "UNTRUSTED_CODE",
        "93174625",
    ):
        assert marker not in visible


def test_raw_success_and_hidden_representations(mode, caplog):
    run, rec, _ = invoke(
        mode,
        httpx.Response(
            200,
            json=GOOD,
            extensions={"http_version": b"VERSION_PRIVATE", "reason_phrase": b"REASON_PRIVATE"},
        ),
    )
    with caplog.at_level("INFO", logger="httpx"):
        res = run()
    assert res.raw == GOOD and res.data == GOOD["data"] and res.message == "***"
    shown = " ".join((str(res), repr(res), res.message, repr(rec.report()), caplog.text))
    for marker in (
        "SSID_PRIVATE",
        "PASSWORD_PRIVATE",
        "IMAGE_PRIVATE",
        MSG,
        TOKEN,
        "VERSION_PRIVATE",
        "REASON_PRIVATE",
        "EXT_PRIVATE",
    ):
        assert marker not in shown
    assert any(
        '"HTTP 200 ***"' in rec.getMessage() for rec in caplog.records if rec.name == "httpx"
    )


def test_timeout(mode):
    run, rec, seen = invoke(mode, httpx.ReadTimeout("TIMEOUT_PRIVATE"))
    with pytest.raises(TransportError) as exc:
        run()
    assert exc.value.reason == "timeout" and len(seen["auth"]) == len(seen["biz"]) == 1


def test_cancellation_propagates():
    run, rec, seen = invoke("async", asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        run()
    assert len(seen["auth"]) == len(seen["biz"]) == 1 and outcomes(rec, "async", "call") == [
        "started",
        "cancelled",
    ]


def block_image_io(monkeypatch, tmp_path):
    import base64
    import binascii
    import builtins
    import io
    import pathlib
    import webbrowser

    def deny(*args, **kwargs):
        pytest.fail("unexpected image operation")

    def wrap(original):
        def inner(file, mode="r", *args, **kwargs):
            if any(flag in mode for flag in ("w", "a", "x", "+")):
                deny()
            if str(file).lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                deny()
            return original(file, mode, *args, **kwargs)

        return inner

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(base64, "b64decode", deny)
    monkeypatch.setattr(binascii, "a2b_base64", deny)
    monkeypatch.setattr(pathlib.Path, "write_bytes", deny)
    monkeypatch.setattr(pathlib.Path, "write_text", deny)
    monkeypatch.setattr(webbrowser, "open", deny)
    monkeypatch.setattr(builtins, "open", wrap(builtins.open))
    monkeypatch.setattr(io, "open", wrap(io.open))


def test_opaque_generation_does_not_process_images(mode, monkeypatch, tmp_path):
    block_image_io(monkeypatch, tmp_path)
    body = {"code": "200", "data": {"imageData": "SYNTH:NOT/AN_IMAGE!?"}}

    run, _, seen = invoke(mode, body)
    response = run()

    assert response.code == "200" and response.raw == body
    assert response.data == body["data"]
    assert len(seen["auth"]) == 1
    assert len(seen["biz"]) == 1
    assert list(tmp_path.iterdir()) == []
