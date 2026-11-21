"""HTTP transports: the single request boundary for sync and async SDK calls."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from types import TracebackType
from urllib.parse import quote, quote_plus, urlsplit

import httpx

from ._request_values import _JsonNull
from .errors import _SENSITIVE_KEYS, TransportError, _registration_secrets, redact
from .models import ApiResponse, ResponsePolicy
from .response import decode_response

__all__ = ["SyncTransport", "AsyncTransport"]

_Scalar = str | int | float | bool | None
ParamMapping = Mapping[str, _Scalar | list[_Scalar] | tuple[_Scalar, ...]]

_SECRET_KEYS = frozenset(key.lower() for key in _SENSITIVE_KEYS)

_REASON_MESSAGES = {
    "timeout": "HTTP request timed out",
    "network": "HTTP transport failed; remote execution status is unknown",
    "closed": "HTTP client is already closed",
}


class _SdkRedactionContext:
    """Mutable per-request redaction state shared through copied contexts.

    ``invalidate`` expires the state in place so contexts copied into
    application-owned tasks or callbacks stop masking once the owning
    request ends, even after cancellation or transport close.

    ``request_url`` holds the in-flight SDK ``httpx.Request.url`` captured
    by this transport's instance-local request event hook; it is only bound
    while media-URL masking applies and is released on ``invalidate``.
    """

    __slots__ = (
        "secrets",
        "mask_media_urls",
        "request_url",
        "active",
        "mask_registration_fields",
        "mask_device_add_details",
        "mask_oauth_details",
    )

    def __init__(
        self,
        secrets: tuple[str, ...],
        mask_media_urls: bool,
        mask_registration_fields: bool = False,
        mask_device_add_details: bool | None = False,
        mask_oauth_details: bool | None = False,
    ) -> None:
        self.secrets = secrets
        self.mask_media_urls = mask_media_urls
        self.mask_registration_fields = bool(mask_registration_fields)
        self.mask_device_add_details = bool(mask_device_add_details)
        self.mask_oauth_details = bool(mask_oauth_details)
        self.request_url: httpx.URL | None = None
        self.active = True

    def invalidate(self) -> None:
        self.secrets = ()
        self.request_url = None
        self.active = False


_SDK_REDACTION: ContextVar[_SdkRedactionContext | None] = ContextVar(
    "ezviz_unified_sdk_redaction", default=None
)

# Private stream identifiers whose standard query encodings are only
# collected for requests that explicitly opt in via redact_query_identifiers.
_QUERY_IDENTIFIER_KEYS = frozenset({"streamid", "recordspaceid"})


class _SdkRedactionFilter(logging.Filter):
    """Mask secrets of the active SDK request on httpx records only."""

    # Supported HTTPX request INFO records carry structural metadata:
    # record.args == (method, request.url object, http_version, status,
    # reason_phrase). While media-URL masking is active only a record whose
    # args[1] *is* (identity, never equality) the URL captured by this
    # transport's request event hook belongs to the in-flight SDK request;
    # independent HTTPX calls that inherited this context -- even to the
    # same URL text -- keep their own log lines, while a child forwarding
    # the original Request object stays masked. Records without that
    # structural shape (custom messages, non-media SDK modes) keep the
    # previous conservative whole-message masking.

    def filter(self, record: logging.LogRecord) -> bool:
        redaction = _SDK_REDACTION.get()
        if redaction is None or not redaction.active:
            return True
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) == 5
            and isinstance(args[0], str)
            and isinstance(url := args[1], httpx.URL)
            and isinstance(args[2], str)
            and isinstance(args[3], int)
            and isinstance(args[4], str)
        ):
            if (
                redaction.mask_media_urls
                or redaction.mask_registration_fields
                or redaction.mask_device_add_details
                or redaction.mask_oauth_details
            ) and url is not redaction.request_url:
                return True
            if redaction.mask_device_add_details or redaction.mask_oauth_details:
                # Owned device-add/OAuth record: the bound request URL may
                # carry the caller-supplied cursor and other detail parameters
                # in its query, which sit outside the collected secrets, so the
                # query is stripped while method, origin, path and status are
                # preserved; the server-controlled reason phrase is free text
                # and is blanked outright.
                http_version = args[2]
                if (
                    redaction.mask_oauth_details
                    or (redaction.mask_device_add_details and redaction.mask_media_urls)
                ) and http_version not in (
                    "HTTP/1.0",
                    "HTTP/1.1",
                    "HTTP/2",
                    "HTTP/3",
                ):
                    # Some protected responses can carry sensitive server-supplied text
                    # in this field. Only display a recognized HTTP version.
                    http_version = "HTTP"
                record.args = (
                    args[0],
                    url.copy_with(query=None),
                    http_version,
                    args[3],
                    "***",
                )
            elif redaction.mask_registration_fields:
                # Owned registration record: the server-controlled reason
                # phrase is free text that may carry data outside the
                # collected secrets, so it is blanked outright; method,
                # URL, http_version and status continue into normal redact.
                record.args = (*args[:4], "***")
        record.msg = redact(
            record.getMessage(),
            redaction.secrets,
            mask_media_urls=redaction.mask_media_urls,
        )
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_SdkRedactionFilter())


def _capture_request_url(request: httpx.Request) -> None:
    """Bind the in-flight SDK request's URL object for log scoping.

    Instance-local ``request`` event hook on the SDK-owned client: it runs
    before transport delegation and stores ``request.url`` on the active
    redaction context so ``_SdkRedactionFilter`` can attribute HTTPX INFO
    records by identity. The first bind wins for the context lifetime, so
    unrelated requests sharing the context can never displace the SDK's
    own URL and loosen its mask.
    """
    redaction = _SDK_REDACTION.get()
    if (
        redaction is not None
        and redaction.active
        and (
            redaction.mask_media_urls
            or redaction.mask_registration_fields
            or redaction.mask_device_add_details
            or redaction.mask_oauth_details
        )
        and redaction.request_url is None
    ):
        redaction.request_url = request.url


async def _capture_request_url_async(request: httpx.Request) -> None:
    """``AsyncClient`` variant with the required async hook signature."""
    _capture_request_url(request)


def _validate_base_url(base_url: str) -> str:
    if not isinstance(base_url, str):
        raise ValueError("base_url must be a string")
    try:
        parts = urlsplit(base_url)
        parts.port  # force lazy port parsing errors now
    except ValueError:
        raise ValueError("base_url must be a valid HTTPS origin") from None
    if (
        parts.scheme != "https"
        or parts.hostname is None
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "base_url must be an HTTPS origin without credentials, path, query or fragment"
        )
    return base_url.rstrip("/")


def _validate_timeout(timeout: object) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a finite positive number of seconds")
    try:
        value = float(timeout)
    except OverflowError:
        raise ValueError("timeout must be a finite positive number of seconds") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a finite positive number of seconds")
    return value


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        raise ValueError("path must be a root-relative path starting with '/'")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise ValueError("path must not carry scheme, authority, query or fragment")


def _collect_policy_resources(value: str, out: set[str]) -> None:
    """Extract only RAM's documented nested resources from encoded form JSON."""
    try:
        parsed = json.loads(value)
    except (ValueError, RecursionError):
        return
    if not isinstance(parsed, dict):
        return
    statements = parsed.get("Statement", [parsed])
    if not isinstance(statements, list):
        return
    for statement in statements:
        if not isinstance(statement, dict):
            continue
        resources = statement.get("Resource")
        if not isinstance(resources, list):
            continue
        for resource in resources:
            if isinstance(resource, str):
                out.add(resource)
                parts = resource.split(":")
                if len(parts) in (2, 3) and parts[0] in {"dev", "cam"}:
                    out.add(parts[1])


def _collect_into(node: object, out: set[str]) -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _SECRET_KEYS and isinstance(value, str):
                out.add(value)
                if key.lower() == "authorization":
                    parts = value.split(None, 1)
                    if len(parts) == 2 and parts[0].lower() in {"bearer", "basic", "token"}:
                        out.add(parts[1])
                elif key.lower() in {"policy", "statement"}:
                    _collect_policy_resources(value, out)
                elif key.lower() in {"filenodeids", "spaceids"}:
                    out.update(part for part in value.split(",") if part)
            elif (
                isinstance(key, str)
                and key.lower() in _SECRET_KEYS
                and isinstance(value, int)
                and not isinstance(value, bool)
            ):
                out.add(str(value))
            else:
                _collect_into(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            _collect_into(item, out)


def _collect_secrets(explicit: Iterable[str], *sources: object) -> tuple[str, ...]:
    collected = {str(secret) for secret in explicit if secret}
    for source in sources:
        _collect_into(source, collected)
    collected.discard("")
    return tuple(sorted(collected))


def _form_serial_secrets(
    base: tuple[str, ...],
    data: Mapping[str, object] | None,
    policy: ResponsePolicy | None,
) -> tuple[str, ...]:
    if policy is None or not policy.redact_form_serials or data is None:
        return base
    secrets = set(base)
    for key, value in data.items():
        if isinstance(value, str) and value and key in ("deviceSerial", "detectorSerial"):
            secrets.add(value)
            secrets.add(quote(value, safe="", errors="surrogatepass"))
            secrets.add(quote_plus(value, safe="", errors="surrogatepass"))
    return tuple(sorted(secrets))


def _augment_query_secrets(
    base: tuple[str, ...],
    params: ParamMapping | None,
    policy: ResponsePolicy | None,
) -> tuple[str, ...]:
    """Augment base secrets with emitted-query encodings only when opted in."""
    if params is None or policy is None:
        return base
    mask_full_query = policy.redact_message_urls and any(
        isinstance(key, str) and key.lower() == "accesstoken" for key in params
    )
    if not mask_full_query and not policy.redact_query_identifiers:
        return base
    collected = set(base)
    if mask_full_query:
        collected.add(str(httpx.QueryParams(dict(params))))
    for key, value in params.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        already_covered = mask_full_query and lowered in _SECRET_KEYS
        explicit_identifier = policy.redact_query_identifiers and lowered in _QUERY_IDENTIFIER_KEYS
        if not already_covered and not explicit_identifier:
            continue
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            if isinstance(item, str) and item:
                collected.add(quote(item, safe=""))
                collected.add(quote_plus(item, safe=""))
    collected.discard("")
    return tuple(sorted(collected))


def _read_payload(response: httpx.Response, *, redact_oauth_details: bool = False) -> object:
    try:
        return response.json()
    except ValueError:
        return None
    except RecursionError:
        if redact_oauth_details:
            return None
        raise


def _split_body(
    json_body: object,
    headers: Mapping[str, str] | None,
) -> tuple[bytes | None, object, dict[str, str] | None]:
    """Map the request body onto httpx ``content``/``json`` plus headers.

    The ``JSON_NULL`` marker becomes ``content=b"null"`` and defaults the
    Content-Type to ``application/json`` unless the caller already set one
    (case-insensitive). Any other value — including ``None`` for "no
    body" — travels through the existing ``json=`` path unchanged.
    """
    bound = dict(headers) if headers is not None else None
    if not isinstance(json_body, _JsonNull):
        return None, json_body, bound
    if bound is None or not any(key.lower() == "content-type" for key in bound):
        bound = {} if bound is None else bound
        bound["Content-Type"] = "application/json"
    return b"null", None, bound


class SyncTransport:
    """One finite-timeout synchronous HTTPS client; no retries, no redirects."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        self._client = httpx.Client(
            base_url=_validate_base_url(base_url),
            timeout=_validate_timeout(timeout),
            transport=transport,
            trust_env=trust_env,
            follow_redirects=False,
            event_hooks={"request": [_capture_request_url]},
        )

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SyncTransport:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: ParamMapping | None = None,
        data: Mapping[str, object] | None = None,
        json: object = None,
        headers: Mapping[str, str] | None = None,
        policy: ResponsePolicy | None = None,
        operation: str = "",
        secrets: Iterable[str] = (),
    ) -> ApiResponse:
        _validate_path(path)
        if data is not None and json is not None:
            raise ValueError("data and json are mutually exclusive")
        base_secrets = _collect_secrets(secrets, params, data, json, headers)
        if self._client.is_closed:
            raise TransportError(
                _REASON_MESSAGES["closed"],
                reason="closed",
                operation=operation,
                secrets=base_secrets,
            )
        all_secrets = _augment_query_secrets(base_secrets, params, policy)
        all_secrets = _form_serial_secrets(all_secrets, data, policy)
        mask_registration_fields = policy is not None and policy.redact_registration_fields
        if mask_registration_fields:
            all_secrets = _registration_secrets(all_secrets, params, data)
        content, json_body, bound_headers = _split_body(json, headers)
        mask_media_urls = policy is not None and policy.redact_media_urls
        context = _SdkRedactionContext(
            all_secrets,
            mask_media_urls,
            mask_registration_fields=mask_registration_fields,
            mask_device_add_details=(policy is not None and policy.redact_device_add_details),
            mask_oauth_details=(policy is not None and policy.redact_oauth_details),
        )
        marker = _SDK_REDACTION.set(context)
        response: httpx.Response | None = None
        failure: str | None = None
        try:
            response = self._client.request(
                method,
                path,
                params=dict(params) if params is not None else None,
                data=dict(data) if data is not None else None,
                content=content,
                json=json_body,
                headers=bound_headers,
            )
        except httpx.TimeoutException:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "network"
        finally:
            context.invalidate()
            _SDK_REDACTION.reset(marker)
        if failure is not None:
            raise TransportError(
                _REASON_MESSAGES[failure],
                reason=failure,
                operation=operation,
                secrets=all_secrets,
            )
        assert response is not None
        return decode_response(
            _read_payload(
                response,
                redact_oauth_details=policy is not None and policy.redact_oauth_details,
            ),
            policy=policy,
            http_status=response.status_code,
            operation=operation,
            secrets=all_secrets,
        )


class AsyncTransport:
    """Async counterpart of SyncTransport with identical request rules."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=_validate_base_url(base_url),
            timeout=_validate_timeout(timeout),
            transport=transport,
            trust_env=trust_env,
            follow_redirects=False,
            event_hooks={"request": [_capture_request_url_async]},
        )

    @property
    def is_closed(self) -> bool:
        return self._client.is_closed

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncTransport:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: ParamMapping | None = None,
        data: Mapping[str, object] | None = None,
        json: object = None,
        headers: Mapping[str, str] | None = None,
        policy: ResponsePolicy | None = None,
        operation: str = "",
        secrets: Iterable[str] = (),
    ) -> ApiResponse:
        _validate_path(path)
        if data is not None and json is not None:
            raise ValueError("data and json are mutually exclusive")
        base_secrets = _collect_secrets(secrets, params, data, json, headers)
        if self._client.is_closed:
            raise TransportError(
                _REASON_MESSAGES["closed"],
                reason="closed",
                operation=operation,
                secrets=base_secrets,
            )
        all_secrets = _augment_query_secrets(base_secrets, params, policy)
        all_secrets = _form_serial_secrets(all_secrets, data, policy)
        mask_registration_fields = policy is not None and policy.redact_registration_fields
        if mask_registration_fields:
            all_secrets = _registration_secrets(all_secrets, params, data)
        content, json_body, bound_headers = _split_body(json, headers)
        mask_media_urls = policy is not None and policy.redact_media_urls
        context = _SdkRedactionContext(
            all_secrets,
            mask_media_urls,
            mask_registration_fields=mask_registration_fields,
            mask_device_add_details=(policy is not None and policy.redact_device_add_details),
            mask_oauth_details=(policy is not None and policy.redact_oauth_details),
        )
        marker = _SDK_REDACTION.set(context)
        response: httpx.Response | None = None
        failure: str | None = None
        try:
            response = await self._client.request(
                method,
                path,
                params=dict(params) if params is not None else None,
                data=dict(data) if data is not None else None,
                content=content,
                json=json_body,
                headers=bound_headers,
            )
        except httpx.TimeoutException:
            failure = "timeout"
        except httpx.HTTPError:
            failure = "network"
        finally:
            context.invalidate()
            _SDK_REDACTION.reset(marker)
        if failure is not None:
            raise TransportError(
                _REASON_MESSAGES[failure],
                reason=failure,
                operation=operation,
                secrets=all_secrets,
            )
        assert response is not None
        return decode_response(
            _read_payload(
                response,
                redact_oauth_details=policy is not None and policy.redact_oauth_details,
            ),
            policy=policy,
            http_status=response.status_code,
            operation=operation,
            secrets=all_secrets,
        )
