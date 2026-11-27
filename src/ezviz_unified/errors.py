"""Typed errors with explicit redaction for rendered diagnostics."""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterable, Iterator, Mapping
from urllib.parse import quote, quote_plus

REDACTED = "***"

_SENSITIVE_KEYS = (
    "accessToken",
    "access_token",
    "appSecret",
    "app_secret",
    "appKey",
    "app_key",
    "token",
    "secret",
    "password",
    "deviceSerial",
    "detectorSerial",
    "verifyCode",
    "validateCode",
    "refresh_token",
    "refreshToken",
    "sign",
    "auth_code",
    "authCode",
    "client_secret",
    "clientSecret",
    "client_id",
    "clientId",
    "open_id",
    "openId",
    "authorization",
    "ipcSerial",
    "deviceName",
    "voiceName",
    "voiceUrl",
    "osdName",
    "osdNames",
    "domain",
    "deviceIp",
    "subSerial",
    "serial",
    "name",
    "ssid",
    "clientIP",
    "localIp",
    "natIp",
    "code",  # Playback request field: device encryption password, not response status.
    "urlId",
    "cardPassword",
    "phone",
    "fromDeviceSerial",
    "toDeviceSerial",
    "accountId",
    "accountName",
    "oldPassword",
    "newPassword",
    "policy",
    "statement",
    "spaceId",
    "spaceIds",
    "spaceName",
    "spaceIdName",
    "fileNodeId",
    "fileNodeIds",
    "tagId",
    "lastId",
    "lastSpaceId",
    "recordSpaceId",
    "deviceLicense",
    "deviceUserName",
    "deviceCode",
    "vehicleLicensePlate",
    "productKey",
    "oneOffPlanId",
    "planId",
    "planName",
    "templateId",
    "templateName",
    "projectId",
    "fileId",
    "taskId",
    "resultSpaceId",
    "consumerId",
    "streamId",
)
_SENSITIVE_PARAM_RE = re.compile(
    r"\b("
    + "|".join((*_SENSITIVE_KEYS, "wifiSsid", "timeZone", "osd", "voiceId"))
    + r")\s*=\s*([^\s&\"']+)",
    re.IGNORECASE,
)
_HTTP_URL_RE = re.compile(r'https?://[^\s"<>]+', re.IGNORECASE)
_MEDIA_URL_RE = re.compile(r'(?:https?|rtmps?)://[^\s"<>]+', re.IGNORECASE)


def _secret_spans(rendered: str, secret: str) -> Iterator[tuple[int, int]]:
    """Yield ``(start, end)`` for every literal ``secret`` occurrence, in order.

    ``str.find`` is stepped one character past each hit so overlapping
    occurrences are preserved.
    """
    start = rendered.find(secret)
    end_offset = len(secret)
    while start != -1:
        yield start, start + end_offset
        start = rendered.find(secret, start + 1)


def _merged_spans(streams: Iterable[Iterator[tuple[int, int]]]) -> Iterator[tuple[int, int]]:
    """Merge sorted span streams, coalescing overlapping or adjacent ranges."""
    current: tuple[int, int] | None = None
    for start, end in heapq.merge(*streams):
        if current is not None and start <= current[1]:
            if end > current[1]:
                current = (current[0], end)
        else:
            if current is not None:
                yield current
            current = (start, end)
    if current is not None:
        yield current


def redact(
    text: object,
    secrets: Iterable[str] = (),
    *,
    mask_urls: bool = False,
    mask_media_urls: bool = False,
) -> str:
    """Mask labelled sensitive values, exact secrets and optionally whole URLs.

    All sensitive ranges are located on the original rendered text, merged and
    masked in a single pass, so secrets cannot corrupt labelled-key matches or
    leave unmasked tails of multiword values.

    ``mask_media_urls`` masks HTTP(S) and RTMP(S) media URLs; when set it takes
    priority over ``mask_urls``, which masks HTTP(S) URLs only.
    """
    rendered = str(text)
    streams: list[Iterator[tuple[int, int]]] = [
        (match.span(2) for match in _SENSITIVE_PARAM_RE.finditer(rendered))
    ]
    if mask_media_urls:
        streams.append(match.span() for match in _MEDIA_URL_RE.finditer(rendered))
    elif mask_urls:
        streams.append(match.span() for match in _HTTP_URL_RE.finditer(rendered))
    streams.extend(
        _secret_spans(rendered, secret)
        for secret in {str(value) for value in secrets if value}
        if secret
    )
    parts: list[str] = []
    cursor = 0
    for start, end in _merged_spans(streams):
        parts.append(rendered[cursor:start])
        parts.append(REDACTED)
        cursor = end
    if not parts:
        return rendered
    parts.append(rendered[cursor:])
    return "".join(parts)


_REGISTRATION_SECRET_KEYS = frozenset(
    {
        "devicelicense",
        "deviceusername",
        "devicecode",
        "vehiclelicenseplate",
        "productkey",
        "devicename",
        "deviceserial",
    }
)
_REGISTRATION_CSV_KEYS = frozenset({"devicecode", "vehiclelicenseplate"})


def _registration_secrets(secrets: Iterable[str], *sources: object) -> tuple[str, ...]:
    """Return ``secrets`` plus URL-encoded variants of registration fields.

    Each positional source may be a mapping or a list/tuple of row
    mappings. Only immediate key/value pairs are inspected against a
    fixed allow-list of registration field names; arbitrary or nested
    keys are never scanned.
    """
    collected = set(secrets)

    def _add(raw: str) -> None:
        if not raw:
            return
        collected.add(raw)
        collected.add(quote(raw, safe="", errors="surrogatepass"))
        collected.add(quote_plus(raw, safe="", errors="surrogatepass"))

    def _scan(row: object) -> None:
        if not isinstance(row, Mapping):
            return
        for key, value in row.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            normalized = key.lower()
            if normalized not in _REGISTRATION_SECRET_KEYS:
                continue
            if normalized in _REGISTRATION_CSV_KEYS:
                _add(value)
                for member in value.split(","):
                    _add(member)
            else:
                _add(value)

    for source in sources:
        if isinstance(source, Mapping):
            _scan(source)
        elif isinstance(source, (list, tuple)):
            for row in source:
                _scan(row)

    return tuple(sorted(collected))


class EzvizError(Exception):
    """Base error carrying diagnostic context; never stores raw payloads."""

    def __init__(
        self,
        message: object = "",
        *,
        code: str | int | None = None,
        http_status: int | None = None,
        operation: str = "",
        secrets: Iterable[str] = (),
    ) -> None:
        secret_values = tuple(str(s) for s in secrets if s)
        self.message: str = redact(message, secret_values)
        self.code: str | int | None = redact(code, secret_values) if isinstance(code, str) else code
        self.http_status: int | None = http_status
        self.operation: str = redact(operation, secret_values)
        super().__init__(self.message)

    def _render(self, value: object) -> str:
        return str(value)

    def __str__(self) -> str:
        context: list[str] = []
        if self.operation:
            context.append(f"operation={self._render(self.operation)}")
        if self.http_status is not None:
            context.append(f"http_status={self.http_status}")
        if self.code is not None:
            context.append(f"code={self._render(self.code)}")
        base = self.message or self.__class__.__name__
        return f"{base} [{'; '.join(context)}]" if context else base

    def __repr__(self) -> str:
        code = None if self.code is None else self._render(self.code)
        return (
            f"{self.__class__.__name__}(message={self.message!r}, code={code!r}, "
            f"http_status={self.http_status!r}, operation={self._render(self.operation)!r})"
        )


class ApiError(EzvizError):
    """HTTP-level or platform business failure."""


class AuthenticationError(ApiError):
    """HTTP 401 or platform authentication code (e.g. 10002)."""

    def __init__(
        self,
        message: object = "",
        *,
        code: str | int | None = None,
        http_status: int | None = None,
        operation: str = "",
        secrets: Iterable[str] = (),
    ) -> None:
        # Retain a boolean semantic marker, never the unredacted diagnostic code.
        self._invalid_token = type(code) in (str, int) and code in (10002, "10002")
        super().__init__(
            message, code=code, http_status=http_status, operation=operation, secrets=secrets
        )

    @property
    def invalid_token(self) -> bool:
        """Whether the original platform status was invalid/expired token 10002."""
        return self._invalid_token


class PermissionDeniedError(ApiError):
    """HTTP 403 or platform permission code (e.g. 10031)."""


class DeviceError(ApiError):
    """Platform succeeded but the device layer reported a failure."""


class ProtocolError(EzvizError):
    """The response body does not match the expected envelope schema."""


class TransportError(EzvizError):
    """A request timed out, failed at the network layer, or used a closed client."""

    def __init__(
        self,
        message: object = "",
        *,
        reason: str,
        operation: str = "",
        secrets: Iterable[str] = (),
    ) -> None:
        self.reason = reason
        super().__init__(message, operation=operation, secrets=secrets)
