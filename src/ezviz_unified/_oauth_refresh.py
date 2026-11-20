"""Overseas OAuth refresh-token grant helpers."""

from dataclasses import dataclass
from typing import Any, NoReturn

from .errors import ProtocolError
from .models import ApiResponse, ResponsePolicy
from .operations import Operation

_REFRESH_OPERATION = Operation(
    "global.oauth.refresh_token",
    "POST",
    "/oauth/token/refreshToken",
    token_location="none",
    regions=frozenset({"overseas"}),
    read_only=False,
    policy=ResponsePolicy(success_values=("200",), redact_oauth_details=True),
)


@dataclass(frozen=True, slots=True, repr=False)
class OAuthRefreshResult:
    """Refresh-grant tokens; repr/str never expose secret material."""

    access_token: str
    refresh_token: str
    open_id: str | None
    expires_in: int | None
    scope: str | None
    state: str | None

    def __repr__(self) -> str:
        return "OAuthRefreshResult(<redacted>)"


def _has_bad_chars(value: str) -> bool:
    return any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F or 0xD800 <= ord(c) <= 0xDFFF for c in value)


def _arg(name: str, value: object, *, allow_blank: bool) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    if _has_bad_chars(value):
        raise ValueError(f"{name} has forbidden characters")
    if not allow_blank and not value.strip():
        raise ValueError(f"{name} must be non-blank")
    return value


def _refresh_form(
    app_key: str,
    refresh_token: str,
    open_id: str,
    bundle_id: str,
    scope: str | None,
    state: str | None,
) -> dict[str, str]:
    form = {
        "grant_type": "refresh_token",
        "client_id": _arg("client_id", app_key, allow_blank=False),
        "refresh_token": _arg("refresh_token", refresh_token, allow_blank=False),
        "open_id": _arg("open_id", open_id, allow_blank=False),
        "bundleId": _arg("bundleId", bundle_id, allow_blank=False),
    }
    if scope is not None:
        form["scope"] = _arg("scope", scope, allow_blank=True)
    if state is not None:
        form["state"] = _arg("state", state, allow_blank=True)
    return form


def _fail(message: str, response: ApiResponse) -> NoReturn:
    raise ProtocolError(
        message, http_status=response.http_status, operation=_REFRESH_OPERATION.name
    ) from None


def _need(data: dict[str, Any], name: str, response: ApiResponse) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip() or _has_bad_chars(value):
        _fail(f"invalid {name}", response)
    return value


def _opt(data: dict[str, Any], name: str, response: ApiResponse) -> str | None:
    if name not in data:
        return None
    value = data[name]
    if not isinstance(value, str) or _has_bad_chars(value):
        _fail(f"invalid {name}", response)
    return value


def _refresh_result(response: ApiResponse, expected_state: str | None) -> OAuthRefreshResult:
    data = response.data
    if not isinstance(data, dict):
        _fail("malformed response data", response)
    state = _opt(data, "state", response)
    if expected_state is not None and state != expected_state:
        _fail("state mismatch", response)
    expires_in = data.get("expires_in")
    if "expires_in" in data and (
        isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in < 0
    ):
        _fail("invalid expires_in", response)
    return OAuthRefreshResult(
        access_token=_need(data, "access_token", response),
        refresh_token=_need(data, "refresh_token", response),
        open_id=_opt(data, "openId", response),
        expires_in=expires_in,
        scope=_opt(data, "scope", response),
        state=state,
    )
