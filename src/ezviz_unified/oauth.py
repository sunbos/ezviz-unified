"""Build overseas OAuth login URLs, parse regional return fragments, and refresh user grants once."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import quote, unquote_plus, urlencode, urlsplit

import httpx

from ._oauth_refresh import (
    _REFRESH_OPERATION,
    OAuthRefreshResult,
    _refresh_form,
    _refresh_result,
)
from ._path import render_path
from .auth_models import RegionConfig, _normalize_origin
from .diagnostic_report import DiagnosticRecorder, _diagnostic_scope
from .transport import AsyncTransport, SyncTransport

_RESPONSE_TYPES = frozenset({"token", "refresh_token"})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
__all__ = ["OAuth", "AsyncOAuth", "OAuthGrant", "OAuthRefreshResult"]
_LOGIN_ORIGIN = "https://openauth.ezvizlife.com"
_LOGIN_PATH_TEMPLATE = "/oauth/ddns/{appKey}"
_ALLOWED_CLIENT_TYPES = (11, 13, 19)
_BUNDLE_ID_CLIENT_TYPES = (11, 13)


@dataclass(frozen=True)
class OAuthGrant:
    """Decoded overseas OAuth redirect grant with an immutable field mapping."""

    access_token: str
    refresh_token: str | None
    open_id: str | None
    expires_in: int | None
    area_domain: str | None
    auth_domain: str | None
    fields: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fields", MappingProxyType(dict(self.fields)))

    def __repr__(self) -> str:
        return "OAuthGrant(<redacted>)"

    def __str__(self) -> str:
        return "OAuthGrant(<redacted>)"


def _decode_field(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("field must be a string")
    i, n = 0, len(raw)
    while i < n:
        if raw[i] == "%":
            if i + 2 >= n or raw[i + 1] not in _HEX_DIGITS or raw[i + 2] not in _HEX_DIGITS:
                raise ValueError("bad percent-escape")
            i += 3
        else:
            i += 1
    try:
        return unquote_plus(raw, encoding="utf-8", errors="strict")
    except (ValueError, UnicodeDecodeError):
        pass
    raise ValueError("bad field encoding")


def _verified_origin(value: str | None, expected: str | None) -> str | None:
    if value is None:
        return None
    try:
        origin, _host = _normalize_origin(value)
    except ValueError:
        origin = None
    if origin is None or origin != expected:
        raise ValueError("origin verification failed")
    return origin


def _redirect_fragment(redirect_url: str, expected_origin: str) -> str:
    if not isinstance(redirect_url, str):
        raise ValueError("redirect URL must be a string")
    for ch in redirect_url:
        o = ord(ch)
        if (
            ch.isspace()
            or ch == "\\"
            or o < 0x20
            or o == 0x7F
            or 0x80 <= o <= 0x9F
            or 0xD800 <= o <= 0xDFFF
        ):
            raise ValueError("bad redirect URL characters")
    if redirect_url.count("#") != 1:
        raise ValueError("redirect URL must contain one fragment delimiter")
    prefix, fragment = redirect_url.split("#")
    if not fragment or "?" in prefix:
        raise ValueError("bad redirect URL structure")
    try:
        parts = urlsplit(prefix)
    except ValueError:
        parts = None
    if parts is None or parts.path != "/oauth/success":
        raise ValueError("bad redirect URL path")
    _verified_origin(parts.scheme + "://" + parts.netloc, expected_origin)
    return fragment


def _credential(value: str | None, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("missing credential")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("blank credential")
    for ch in value:
        o = ord(ch)
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F:
            raise ValueError("bad credential characters")
    return value


def _expires(value: str | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or any(not "0" <= ch <= "9" for ch in value):
        raise ValueError("bad expires value")
    try:
        return int(value)
    except ValueError:
        pass
    raise ValueError("bad expires value")


def _parse(
    redirect_url: str,
    response_type: str,
    expected_state: str | None,
    region: RegionConfig,
) -> OAuthGrant:
    """Validate a fixed overseas OAuth redirect URL into an OAuthGrant."""
    if not isinstance(response_type, str) or response_type not in _RESPONSE_TYPES:
        raise ValueError("unsupported response_type")
    if expected_state is not None and not isinstance(expected_state, str):
        raise TypeError("expected_state must be a string")
    fragment = _redirect_fragment(redirect_url, region.auth_base_url)
    fields: dict[str, str] = {}
    for segment in fragment.split("&"):
        raw_name, separator, raw_value = segment.partition("=")
        if not separator:
            raise ValueError("redirect fragment field is missing '='")
        name = _decode_field(raw_name)
        if not name:
            raise ValueError("redirect fragment field name is empty")
        if name in fields:
            raise ValueError("redirect fragment field is duplicated")
        fields[name] = _decode_field(raw_value)
    if expected_state is not None and fields.get("state") != expected_state:
        raise ValueError("redirect state mismatch")
    _credential(fields.get("access_token"), required=True)
    refresh_token = _credential(
        fields.get("refresh_token"), required=response_type == "refresh_token"
    )
    open_id = _credential(fields.get("open_id"), required=False)
    return OAuthGrant(
        access_token=fields["access_token"],
        refresh_token=refresh_token,
        open_id=open_id,
        expires_in=_expires(fields.get("expires_in")),
        area_domain=_verified_origin(fields.get("areaDomain"), region.api_base_url),
        auth_domain=_verified_origin(fields.get("authDomain"), region.auth_base_url),
        fields=fields,
    )


def _build_login_url(
    login_path: str,
    area_id: int,
    client_type: int,
    response_type: str,
    bundle_id: str | None,
) -> str:
    if isinstance(area_id, bool) or not isinstance(area_id, int):
        raise TypeError("area_id must be an integer")
    if area_id <= 0:
        raise ValueError("area_id must be a positive integer")
    if isinstance(client_type, bool) or not isinstance(client_type, int):
        raise TypeError("client_type must be an integer")
    if client_type not in _ALLOWED_CLIENT_TYPES:
        raise ValueError("client_type must be one of 11, 13 or 19")
    if not isinstance(response_type, str):
        raise TypeError("response_type must be a string")
    if response_type not in _RESPONSE_TYPES:
        raise ValueError("response_type must be 'token' or 'refresh_token'")
    if bundle_id is None:
        if client_type in _BUNDLE_ID_CLIENT_TYPES:
            raise ValueError("bundle_id is required for this client_type")
    elif not isinstance(bundle_id, str):
        raise TypeError("bundle_id must be a string")
    else:
        if not bundle_id.strip():
            raise ValueError("bundle_id must not be blank")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in bundle_id):
            raise ValueError("bundle_id must be valid UTF-8")
    try:
        area = str(area_id)
    except ValueError:
        area = ""
    if not area:
        raise ValueError("invalid area_id")
    fields = [
        ("areaId", area),
        ("client_type", str(client_type)),
        ("response_type", response_type),
    ]
    if bundle_id is not None:
        fields.append(("bundleId", bundle_id))
    query = urlencode(fields, safe="", quote_via=quote)
    return f"{_LOGIN_ORIGIN}{login_path}?{query}"


class _OAuthBase:
    def __init__(
        self,
        app_key: str,
        *,
        auth_origin: str,
        api_origin: str,
        diagnostic_recorder: DiagnosticRecorder | None = None,
    ) -> None:
        if diagnostic_recorder is not None and type(diagnostic_recorder) is not DiagnosticRecorder:
            raise TypeError("diagnostic_recorder must be a DiagnosticRecorder")
        if not isinstance(auth_origin, str):
            raise TypeError("auth_origin must be a string")
        if not auth_origin.strip():
            raise ValueError("auth_origin must be non-empty")
        if not isinstance(api_origin, str):
            raise TypeError("api_origin must be a string")
        if not api_origin.strip():
            raise ValueError("api_origin must be non-empty")
        if not isinstance(app_key, str):
            raise TypeError("app_key must be a string")
        if not app_key.strip():
            raise ValueError("app_key must be non-empty")
        self._login_path, _ = render_path(_LOGIN_PATH_TEMPLATE, ("appKey",), {"appKey": app_key})
        self._region = RegionConfig.overseas(auth_base_url=auth_origin, api_base_url=api_origin)
        if self._region.auth_base_url == self._region.api_base_url:
            raise ValueError("auth_origin and api_origin must be different origins")
        self._diagnostic_recorder = diagnostic_recorder
        self._app_key = app_key

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


class OAuth(_OAuthBase):
    def refresh_token(
        self,
        refresh_token: str,
        *,
        open_id: str,
        bundle_id: str,
        scope: str | None = None,
        state: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> OAuthRefreshResult:
        """Refresh a user grant once and close the injected transport after the request."""
        with _diagnostic_scope(
            self,
            entrypoint="oauth.refresh_token",
            mode="sync",
            operation=_REFRESH_OPERATION,
        ):
            form = _refresh_form(
                self._app_key,
                refresh_token,
                open_id,
                bundle_id,
                scope,
                state,
            )
            if transport is not None and not isinstance(transport, httpx.BaseTransport):
                raise TypeError("transport must be an httpx.BaseTransport instance")
            with SyncTransport(
                self._region.auth_base_url,
                timeout=timeout,
                transport=transport,
                trust_env=False,
            ) as client:
                with _diagnostic_scope(
                    self,
                    entrypoint="oauth.refresh_token",
                    mode="sync",
                    kind="attempt",
                    attempt=1,
                    operation=_REFRESH_OPERATION,
                ):
                    reply = client.request(
                        _REFRESH_OPERATION.method,
                        _REFRESH_OPERATION.path,
                        data=form,
                        policy=_REFRESH_OPERATION.policy,
                        operation=_REFRESH_OPERATION.name,
                    )
                    return _refresh_result(reply, state)

    def build_login_url(
        self,
        area_id: int,
        *,
        client_type: int,
        response_type: str = "token",
        bundle_id: str | None = None,
    ) -> str:
        with _diagnostic_scope(self, entrypoint="oauth.build_login_url", mode="sync"):
            return _build_login_url(
                self._login_path, area_id, client_type, response_type, bundle_id
            )

    def parse_redirect(
        self,
        redirect_url: str,
        *,
        response_type: str = "token",
        expected_state: str | None = None,
    ) -> OAuthGrant:
        with _diagnostic_scope(self, entrypoint="oauth.parse_redirect", mode="sync"):
            return _parse(redirect_url, response_type, expected_state, self._region)


class AsyncOAuth(_OAuthBase):
    async def refresh_token(
        self,
        refresh_token: str,
        *,
        open_id: str,
        bundle_id: str,
        scope: str | None = None,
        state: str | None = None,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> OAuthRefreshResult:
        """Refresh a user grant once and close the injected transport after the request."""
        with _diagnostic_scope(
            self,
            entrypoint="oauth.refresh_token",
            mode="async",
            operation=_REFRESH_OPERATION,
        ):
            form = _refresh_form(
                self._app_key,
                refresh_token,
                open_id,
                bundle_id,
                scope,
                state,
            )
            if transport is not None and not isinstance(transport, httpx.AsyncBaseTransport):
                raise TypeError("transport must be an httpx.AsyncBaseTransport instance")
            async with AsyncTransport(
                self._region.auth_base_url,
                timeout=timeout,
                transport=transport,
                trust_env=False,
            ) as client:
                with _diagnostic_scope(
                    self,
                    entrypoint="oauth.refresh_token",
                    mode="async",
                    kind="attempt",
                    attempt=1,
                    operation=_REFRESH_OPERATION,
                ):
                    reply = await client.request(
                        _REFRESH_OPERATION.method,
                        _REFRESH_OPERATION.path,
                        data=form,
                        policy=_REFRESH_OPERATION.policy,
                        operation=_REFRESH_OPERATION.name,
                    )
                    return _refresh_result(reply, state)

    async def build_login_url(
        self,
        area_id: int,
        *,
        client_type: int,
        response_type: str = "token",
        bundle_id: str | None = None,
    ) -> str:
        with _diagnostic_scope(self, entrypoint="oauth.build_login_url", mode="async"):
            return _build_login_url(
                self._login_path, area_id, client_type, response_type, bundle_id
            )

    async def parse_redirect(
        self,
        redirect_url: str,
        *,
        response_type: str = "token",
        expected_state: str | None = None,
    ) -> OAuthGrant:
        with _diagnostic_scope(self, entrypoint="oauth.parse_redirect", mode="async"):
            return _parse(redirect_url, response_type, expected_state, self._region)
