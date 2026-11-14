import asyncio
import importlib
import inspect
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    EzvizError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)


def example(kind):
    path = Path(__file__).parents[1] / "examples" / f"devices_{kind}.py"
    assert path.is_file(), "Runnable device example missing"
    return importlib.import_module(f"examples.devices_{kind}")


def _common_module():
    example("sync")  # Ensure the example files exist before importing helpers.
    return importlib.import_module("examples._common")


def _run(module):
    returned = module.main()
    return asyncio.run(returned) if inspect.isawaitable(returned) else returned


def _make_sdk_error(exc_cls, code=None, http_status=None):
    """Instantiate a real SDK exception via its public constructor."""
    if exc_cls is TransportError:
        return TransportError("PRIVATE_MESSAGE", reason="network")
    return exc_cls("PRIVATE_MESSAGE", code=code, http_status=http_status)


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("scope", ["domestic", "overseas"])
@pytest.mark.parametrize("result", ["device", "empty", "wrong_shape", "auth_error"])
def test_examples_use_real_sdk_for_one_page_and_keep_output_private(
    monkeypatch, capsys, kind, scope, result
):
    module = example(kind)
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_KEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_SECRET")
    monkeypatch.setenv("EZVIZ_REGION", scope)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    auth_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezvizlife.com"
    api_host = "open.ys7.com" if scope == "domestic" else "isgpopen.ezviz.com"
    if scope == "overseas":
        monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", f"https://{auth_host}")
    calls = []

    def auth(request):
        calls.append("auth")
        assert request.url.host == auth_host
        if result == "auth_error":
            return httpx.Response(200, json={"code": "10017", "msg": "PRIVATE_ERROR"})
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "PRIVATE_TOKEN",
                    "expireTime": 9_999_999_999_999,
                    "areaDomain": f"https://{api_host}",
                },
            },
        )

    def business(request):
        calls.append("list")
        assert request.url.host == api_host
        assert request.method == "POST" and request.url.path == "/api/lapp/device/list"
        data = parse_qs(request.content.decode())
        assert data == {"accessToken": ["PRIVATE_TOKEN"], "pageStart": ["0"], "pageSize": ["1"]}
        payload = {"PRIVATE": "unexpected"} if result == "wrong_shape" else []
        if result == "device":
            payload = [{"deviceSerial": "PRIVATE_SERIAL", "deviceName": "PRIVATE_NAME"}]
        return httpx.Response(200, json={"code": "200", "data": payload})

    cls = Client if kind == "sync" else AsyncClient
    monkeypatch.setattr(
        module,
        cls.__name__,
        lambda *args, **kwargs: cls(
            *args,
            **kwargs,
            auth_transport=httpx.MockTransport(auth),
            transport_factory=lambda origin: httpx.MockTransport(business),
        ),
    )
    returned = module.main()
    code = asyncio.run(returned) if inspect.isawaitable(returned) else returned
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    if result in {"device", "empty"}:
        assert code == 0 and captured.err == ""
        report = json.loads(captured.out)
        assert report["returned_items"] == (1 if result == "device" else 0)
        assert report["page_start"] == 0 and report["page_size"] == 1
        assert report["region"] == scope
    else:
        assert code != 0 and captured.out == ""
        report = json.loads(captured.err)
        assert report["status"] == "failed"
        if result == "auth_error":
            assert str(report["code"]) == "10017"
    assert calls == (["auth"] if result == "auth_error" else ["auth", "list"])


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "invalid",
    ["missing_key", "missing_secret", "missing_region", "bad_region", "domestic_override"],
)
def test_invalid_example_configuration_stops_before_client_creation(
    monkeypatch, capsys, kind, invalid
):
    module = example(kind)
    for key, value in {
        "EZVIZ_APP_KEY": "PRIVATE_KEY",
        "EZVIZ_APP_SECRET": "PRIVATE_SECRET",
        "EZVIZ_REGION": "domestic",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    if invalid.startswith("missing_"):
        name = {
            "missing_key": "EZVIZ_APP_KEY",
            "missing_secret": "EZVIZ_APP_SECRET",
            "missing_region": "EZVIZ_REGION",
        }[invalid]
        monkeypatch.delenv(name)
    elif invalid == "bad_region":
        monkeypatch.setenv("EZVIZ_REGION", "PRIVATE_INVALID")
    else:
        monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", "https://isgpopen.ezvizlife.com")

    def forbidden(*args, **kwargs):
        pytest.fail("Client created before configuration validation")

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", forbidden)
    returned = module.main()
    code = asyncio.run(returned) if inspect.isawaitable(returned) else returned
    captured = capsys.readouterr()
    assert code != 0 and captured.out == ""
    assert json.loads(captured.err)["status"] == "failed"
    assert "PRIVATE" not in captured.err


def test_example_config_repr_never_exposes_credentials(monkeypatch):
    common = _common_module()
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_APP_KEY_9f3d")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_APP_SECRET_9f3d")
    monkeypatch.setenv("EZVIZ_REGION", "domestic")
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    config = common.load_config()
    rendered = repr(config)
    assert "domestic" in rendered
    assert "PRIVATE_APP_KEY_9f3d" not in rendered
    assert "PRIVATE_APP_SECRET_9f3d" not in rendered


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    ("scope", "override"),
    [
        ("overseas", ""),  # Set-but-empty is an error, not "unset".
        ("overseas", "  \t "),  # Whitespace-only is an error too.
        ("domestic", ""),  # Domestic rejects any set value, even empty.
        ("overseas", "PRIVATE_OVERRIDE_URL"),  # SDK URL validation -> config_error.
    ],
)
def test_set_auth_base_url_is_strictly_validated(monkeypatch, capsys, kind, scope, override):
    module = example(kind)
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_APP_KEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_APP_SECRET")
    monkeypatch.setenv("EZVIZ_REGION", scope)
    monkeypatch.setenv("EZVIZ_AUTH_BASE_URL", override)

    def forbidden(*args, **kwargs):
        pytest.fail("Client created before configuration validation")

    monkeypatch.setattr(module, "Client" if kind == "sync" else "AsyncClient", forbidden)
    code = _run(module)
    captured = capsys.readouterr()
    assert code != 0 and captured.out == ""
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": "config_error",
        "field": "EZVIZ_AUTH_BASE_URL",
    }
    assert "PRIVATE" not in captured.err


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_unset_auth_base_url_uses_documented_overseas_default(monkeypatch, capsys, kind):
    module = example(kind)
    monkeypatch.setenv("EZVIZ_APP_KEY", "PRIVATE_APP_KEY")
    monkeypatch.setenv("EZVIZ_APP_SECRET", "PRIVATE_APP_SECRET")
    monkeypatch.setenv("EZVIZ_REGION", "overseas")
    monkeypatch.delenv("EZVIZ_AUTH_BASE_URL", raising=False)
    auth_hosts = []

    def auth(request):
        auth_hosts.append(request.url.host)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {
                    "accessToken": "PRIVATE_TOKEN",
                    "expireTime": 9_999_999_999_999,
                    "areaDomain": "https://api.ezvizlife.com",
                },
            },
        )

    def business(request):
        return httpx.Response(200, json={"code": "200", "data": []})

    cls = Client if kind == "sync" else AsyncClient
    monkeypatch.setattr(
        module,
        cls.__name__,
        lambda *args, **kwargs: cls(
            *args,
            **kwargs,
            auth_transport=httpx.MockTransport(auth),
            transport_factory=lambda origin: httpx.MockTransport(business),
        ),
    )
    assert _run(module) == 0
    captured = capsys.readouterr()
    assert "PRIVATE" not in captured.out + captured.err
    assert auth_hosts == ["open.ezvizlife.com"]


def test_sdk_protocol_and_transport_errors_are_ezviz_errors():
    assert issubclass(ProtocolError, EzvizError)
    assert issubclass(TransportError, EzvizError)


@pytest.mark.parametrize(
    "exc_cls",
    [
        AuthenticationError,
        PermissionDeniedError,
        ProtocolError,
        TransportError,
        ApiError,
        EzvizError,
    ],
)
def test_print_failure_names_real_sdk_exception_and_redacts_message(capsys, exc_cls):
    common = _common_module()
    exc = _make_sdk_error(exc_cls, code="PRIVATE_CODE", http_status="PRIVATE_STATUS")
    common.print_failure(exc)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": exc_cls.__name__,
    }
    assert "PRIVATE" not in captured.err


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("10002", "10002"),  # ASCII-digit strings are kept verbatim.
        (10017, 10017),  # Real ints are kept.
        (True, "ABSENT"),  # bool is not a business code.
        ("100A", "ABSENT"),  # Letters disqualify the whole code.
        ("\uff11\uff10\uff10\uff12", "ABSENT"),  # Full-width digits are not ASCII.
        ("\u00b20", "ABSENT"),  # isdigit() is True but the char is not ASCII.
        ("", "ABSENT"),
        (None, "ABSENT"),
    ],
)
def test_print_failure_keeps_only_ascii_digit_codes(capsys, code, expected):
    common = _common_module()
    exc = _make_sdk_error(ApiError, code=code, http_status=True)
    common.print_failure(exc)
    report = json.loads(capsys.readouterr().err)
    assert report["error_type"] == "ApiError"
    if expected == "ABSENT":
        assert "code" not in report
    else:
        assert report["code"] == expected
    assert "http_status" not in report  # bool status is excluded as well.


def test_print_failure_keeps_integer_http_status(capsys):
    common = _common_module()
    common.print_failure(_make_sdk_error(ApiError, http_status=503))
    assert json.loads(capsys.readouterr().err) == {
        "status": "failed",
        "error_type": "ApiError",
        "http_status": 503,
    }


def test_print_failure_config_error_and_non_sdk_error(capsys):
    common = _common_module()
    common.print_failure(common.ConfigError("EZVIZ_APP_KEY"))
    assert json.loads(capsys.readouterr().err) == {
        "status": "failed",
        "error_type": "config_error",
        "field": "EZVIZ_APP_KEY",
    }
    common.print_failure(ValueError("PRIVATE_PAYLOAD"))
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {
        "status": "failed",
        "error_type": "unexpected_response",
    }
    assert "PRIVATE" not in captured.err
