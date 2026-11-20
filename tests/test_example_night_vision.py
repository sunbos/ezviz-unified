"""Offline contract tests for ``examples.night_vision.main`` (synthetic env only)."""

import asyncio
import json

import httpx
import pytest

import examples.night_vision as night_vision

USAGE = "usage: python -m examples.night_vision --mode sync|async\n"
ERR = "error: night vision query failed\n"
ENV = {
    "EZVIZ_REGION": "domestic",
    "EZVIZ_DEVICE_SERIAL": "Mixed-Unit",
    "EZVIZ_CHANNEL_NO": "-7",
    "EZVIZ_APP_KEY": "SynApp",
    "EZVIZ_APP_SECRET": "SynSecret",
}
TOKEN_ROOT = {
    "code": "200",
    "data": {
        "accessToken": "TOK1",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
}


@pytest.fixture
def env(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    return monkeypatch


class _Rig:
    """Real Client/AsyncClient backed by recording MockTransports."""

    def __init__(self):
        self.calls, self.auth_requests, self.biz_requests = [], [], []
        self.value_info, self.biz_hook = "0", None

    def _auth(self, request):
        self.auth_requests.append(request)
        return httpx.Response(200, json=TOKEN_ROOT)

    def _biz(self, request):
        self.biz_requests.append(request)
        if self.biz_hook is not None:
            return self.biz_hook(request)
        return httpx.Response(
            200,
            json={"meta": {"code": 200, "message": "ok"}, "data": {"valueInfo": self.value_info}},
        )

    def wrap(self, real, kind):
        def factory(app_key, app_secret, *, region):
            self.calls.append((kind, app_key, app_secret, region))
            return real(
                app_key,
                app_secret,
                region=region,
                auth_transport=httpx.MockTransport(self._auth),
                transport_factory=lambda origin: httpx.MockTransport(self._biz),
            )

        return factory


@pytest.fixture
def rig(env, monkeypatch):
    state = _Rig()
    monkeypatch.setattr(night_vision, "Client", state.wrap(night_vision.Client, "sync"))
    monkeypatch.setattr(night_vision, "AsyncClient", state.wrap(night_vision.AsyncClient, "async"))
    return state


def _check_biz(request):
    assert request.method == "GET"
    assert request.url.path == "/api/v3/keyValue/Mixed-Unit/-7/op"
    assert sorted(request.url.params.multi_items()) == [
        ("accessToken", "TOK1"),
        ("key", "NightVision_Model"),
    ]
    assert request.content == b""
    assert "Content-Type" not in request.headers
    assert "authorization" not in request.headers
    assert "accessToken" not in request.headers
    assert "deviceSerial" not in request.headers


@pytest.mark.parametrize("mode", ["sync", "async"])
@pytest.mark.parametrize(
    "value_info,kind",
    [("1", "string"), ('{"night": true}', "string"), ({"night": "CANARY"}, "object")],
)
def test_ok(rig, capsys, mode, value_info, kind):
    rig.value_info = value_info
    assert night_vision.main(["--mode", mode]) == 0
    out, err = capsys.readouterr()
    assert err == "" and out.count("\n") == 1 and "CANARY" not in out
    assert json.loads(out) == {
        "region": "domestic",
        "mode": mode,
        "status": "ok",
        "value_info_type": kind,
    }
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1
    _check_biz(rig.biz_requests[0])
    assert [(c[0], c[1], c[2]) for c in rig.calls] == [(mode, "SynApp", "SynSecret")]


@pytest.mark.parametrize("argv", [["-h"], ["--help"]])
def test_help(rig, capsys, argv, monkeypatch):
    monkeypatch.setattr(
        night_vision,
        "load_config",
        lambda *a, **k: pytest.fail("load_config must not be called"),
    )
    assert night_vision.main(argv) == 0
    out, err = capsys.readouterr()
    assert out == USAGE and err == ""
    assert rig.calls == [] and rig.auth_requests == [] and rig.biz_requests == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode"],
        ["--mode", "bogus"],
        ["--mode", "sync", "--verbose"],
        ["sync", "x"],
        ["--mode", "sync", "--set", "1"],
    ],
)
def test_bad_argv(rig, capsys, argv, monkeypatch):
    monkeypatch.setattr(
        night_vision,
        "load_config",
        lambda *a, **k: pytest.fail("load_config must not be called"),
    )
    assert night_vision.main(argv) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == USAGE
    assert rig.calls == [] and rig.biz_requests == []


@pytest.mark.parametrize(
    "key,value",
    [
        ("EZVIZ_REGION", None),
        ("EZVIZ_REGION", "overseas"),
        ("EZVIZ_DEVICE_SERIAL", None),
        ("EZVIZ_DEVICE_SERIAL", ""),
        ("EZVIZ_CHANNEL_NO", None),
        ("EZVIZ_CHANNEL_NO", "1.2"),
        ("EZVIZ_CHANNEL_NO", "1_0"),
        ("EZVIZ_APP_KEY", None),
        ("EZVIZ_APP_SECRET", None),
    ],
)
def test_bad_config(rig, env, capsys, key, value):
    if value is None:
        env.delenv(key, raising=False)
    else:
        env.setenv(key, value)
    assert night_vision.main(["--mode", "sync"]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == ERR
    assert rig.calls == [] and rig.auth_requests == [] and rig.biz_requests == []


@pytest.mark.parametrize(
    "mode,root",
    [
        (
            "sync",
            {
                "meta": {"code": 10031, "message": "PRIVATE-ECHO"},
                "data": {"valueInfo": "PRIVATE-ECHO"},
            },
        ),
        (
            "async",
            {
                "meta": {"code": 200, "message": "PRIVATE-ECHO"},
                "data": {"valueInfo": None, "upstream": "PRIVATE-ECHO"},
            },
        ),
    ],
)
def test_query_failure(rig, capsys, mode, root):
    rig.biz_hook = lambda request: httpx.Response(200, json=root)
    assert night_vision.main(["--mode", mode]) == 1
    out, err = capsys.readouterr()
    assert out == "" and err == ERR and "PRIVATE" not in err
    assert len(rig.auth_requests) == 1 and len(rig.biz_requests) == 1


@pytest.mark.parametrize(
    "mode,exc", [("sync", KeyboardInterrupt), ("async", asyncio.CancelledError)]
)
def test_interrupted(rig, capsys, mode, exc):
    def hook(request):
        raise exc

    rig.biz_hook = hook
    assert night_vision.main(["--mode", mode]) == 130
    out, err = capsys.readouterr()
    assert out == "" and err == ERR
