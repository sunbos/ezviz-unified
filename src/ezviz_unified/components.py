"""Reviewed domestic component-path catalogue (path-style OTAP endpoints).

This catalogue mirrors a small set of reviewed open.ys7.com documents that
place ``deviceSerial`` inside the request path
(``/api/v3/otap/{prop,action}/{deviceSerial}/...``). It is a documentation
record, not a device capability probe: the SDK never queries a model,
never enumerates devices, and never substitutes one resource path for
another. The path-style operations here share transport, authentication,
encoding, and response checking with the header-style ``otap`` facade,
but no document declares the two styles equivalent; whether they are
interchangeable is not established by the reviewed sources, so they
remain independent contracts.

Each entry is backed by a re-fetched official page whose body hash matches
the archive (774, 775, 812, 847, 868, 998, 1134, 1447, 3873). Seven are
callable through :meth:`Components.call` / :meth:`AsyncComponents.call`;
two are kept as non-callable metadata: doc 847 formats a local disk
(destructive, and the doc gives no disk-selection rule) and doc 3873 uses
an unconfirmed ``{{deviceSerial}}`` / ``["0"]`` template spelling. ``call``
on either raises a fixed ``ValueError`` before authentication, and an
unknown ``operation_id`` raises the same error without echoing the input.

Payload rules follow the docs literally: no-body operations refuse any
payload; the doc 998 "query" action requires an explicit JSON ``null``
body (``payload=None``); doc 775 accepts only the optional Boolean
``enabled`` field; doc 868 accepts only an integer in ``[0, 100]`` with
booleans rejected. Docs 774, 812, 1134, and 1447 declare
``Content-Type: application/json`` in their parameter tables and curl
examples, so that header is sent even though those GETs carry no body.
When a response carries ``meta.moreInfo.deviceMeta`` its ``code`` must
equal ``"0x00000000"``; an absent layer is weaker evidence and does not
prove physical execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from ._request_values import JSON_NULL
from .diagnostic_report import _diagnostic_scope
from .models import ApiResponse, ResponsePolicy
from .operations import Operation

if TYPE_CHECKING:
    from .client import AsyncClient, Client

__all__ = ["AsyncComponents", "ComponentInfo", "Components"]

_DOMESTIC = "domestic"
_JSON_CONTENT_TYPE = "application/json"

# ``meta.moreInfo.deviceMeta`` is a second status layer whose documented
# strength differs by doc: only doc 1447's return table explicitly defines
# ``0x00000000`` as the device success code, while the other pages show it
# in success examples without stating the layer is always present.
# Treating it as optional is the SDK's handling policy, not a guarantee
# each doc makes: when present the code must spell "0x00000000"; when
# absent the platform meta.code alone decides — weaker evidence that does
# not prove the device physically executed.
_POLICY = ResponsePolicy(
    code_path="meta.code",
    message_path="meta.message",
    data_path="data",
    success_values=(200,),
    device_code_path="meta.moreInfo.deviceMeta.code",
    device_message_path="meta.moreInfo.deviceMeta.errorMsg",
    device_success_values=("0x00000000",),
    require_device_code=False,
)

# Callable operations are module-level constants so the rendered template
# stays auditable; ``Operation.name`` is the full catalogue operation_id.
_OP_FACE_VOICE_VIDEO_GET = Operation(
    "cn.components.face_voice_video.get",
    "GET",
    "/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/FaceRecognitionVoiceTipSwitch",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_FACE_VOICE_VIDEO_SET = Operation(
    "cn.components.face_voice_video.set",
    "PUT",
    "/api/v3/otap/prop/{deviceSerial}/Video/1/DoorLockCtrl/FaceRecognitionVoiceTipSwitch",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    policy=_POLICY,
)
_OP_FACE_VOICE_LOCK_GET = Operation(
    "cn.components.face_voice_lock.get",
    "GET",
    "/api/v3/otap/prop/{deviceSerial}/DoorLock/0/DoorLockCtrl/FaceRecognitionVoiceTipSwitch",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_TEMPERATURE_GET = Operation(
    "cn.components.temperature.get",
    "GET",
    "/api/v3/otap/prop/{deviceSerial}/global/0/Humiture/Temperature",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_SMOKE_COUNTDOWN_GET = Operation(
    "cn.components.smoke_countdown.get",
    "GET",
    "/api/v3/otap/prop/{deviceSerial}/SmokeSensor/0/PowerMgr/CountdownInfo",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
# Doc 998 declares a countdown query ("该接口用于查询倒计时信息") yet issues
# PUT with a literal null body; the HTTP method is the documented
# transport, not a write action, so read_only=True and an expired-token
# failure may refresh once and replay the identical request once.
_OP_GAS_COUNTDOWN_QUERY = Operation(
    "cn.components.gas_countdown.query",
    "PUT",
    "/api/v3/otap/action/{deviceSerial}/GasSensor/0/PowerMgr/GetPowerSwitchCountdown",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    read_only=True,
    policy=_POLICY,
)
_OP_CHIME_POWER_SET = Operation(
    "cn.components.chime_power.set",
    "PUT",
    "/api/v3/otap/prop/{deviceSerial}/Default/0/PowerMgr/SurplusPower",
    token_location="header",
    regions=frozenset({_DOMESTIC}),
    policy=_POLICY,
)


def _prepare_face_voice_body(payload: object) -> dict[str, object]:
    """Doc 775 body: only the optional Boolean ``enabled`` field.

    The field may be omitted per the doc, so an empty mapping is accepted
    without claiming what the device does with it. The mapping is copied
    once so the caller's object is never mutated; with a one-field schema
    a strict key/value check is sufficient.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be a mapping")
    for key, value in payload.items():
        if not isinstance(key, str):
            raise ValueError("payload keys must be strings")
        if key != "enabled":
            raise ValueError("payload only supports the documented 'enabled' field")
        if not isinstance(value, bool):
            raise ValueError("payload 'enabled' must be a Boolean")
    return dict(payload)


def _prepare_chime_power_body(payload: object) -> int:
    """Doc 868 body: a bare integer in the documented range [0, 100].

    The value is forwarded unchanged — no unit conversion and no claim
    that it alters physical battery capacity.
    """
    if isinstance(payload, bool) or not isinstance(payload, int):
        raise ValueError("payload must be an integer in [0, 100]")
    if not 0 <= payload <= 100:
        raise ValueError("payload must be an integer in [0, 100]")
    return payload


class _Sentinel:
    """Private "payload not provided" marker distinct from ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Sentinel()

_NO_PAYLOAD = "payload_not_accepted"
_EXPLICIT_NULL = "explicit_null"
_FACE_VOICE = "face_voice_mapping"
_CHIME_POWER = "chime_power_integer"


class _Entry(NamedTuple):
    """Catalogue row: documentation metadata plus the callable contract."""

    info: ComponentInfo
    operation: Operation | None
    payload_kind: str | None


@dataclass(frozen=True)
class ComponentInfo:
    """Documentation-backed description of one catalogue entry.

    ``whitelist_required`` is ``None`` when the source does not state a
    whitelist requirement — unknown, not ``False``. ``read_only_evidence``
    quotes the doc wording that justifies the ``read_only`` flag. The
    documented models are not evidence that other models are unsupported.
    """

    operation_id: str
    source_doc_id: str
    source_url: str
    raw_path: str
    method: str
    documented_models: tuple[str, ...]
    whitelist_required: bool | None
    read_only: bool
    read_only_evidence: str
    callable: bool
    limitations: tuple[str, ...]


def _component_entry(
    operation: Operation,
    source_doc_id: str,
    *,
    documented_models: tuple[str, ...],
    whitelist_required: bool | None,
    read_only: bool,
    read_only_evidence: str,
    limitations: tuple[str, ...],
    payload_kind: str,
) -> _Entry:
    info = ComponentInfo(
        operation_id=operation.name,
        source_doc_id=source_doc_id,
        source_url=f"https://open.ys7.com/help/{source_doc_id}",
        raw_path=operation.path,
        method=operation.method,
        documented_models=documented_models,
        whitelist_required=whitelist_required,
        read_only=read_only,
        read_only_evidence=read_only_evidence,
        callable=True,
        limitations=limitations,
    )
    return _Entry(info, operation, payload_kind)


_CATALOGUE: tuple[_Entry, ...] = (
    _component_entry(
        _OP_FACE_VOICE_VIDEO_GET,
        "774",
        documented_models=("CS-DL30-V100系列", "CS-Y3000F-V100系列"),
        whitelist_required=True,
        read_only=True,
        read_only_evidence="doc 774 declares the query function for the voice-tip switch",
        limitations=(
            "documented only for CS-DL30-V100 series and CS-Y3000F-V100 series smart locks",
            "doc states a whitelist must be configured with EZVIZ or the call may fail",
            "doc 774 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_NO_PAYLOAD,
    ),
    _component_entry(
        _OP_FACE_VOICE_VIDEO_SET,
        "775",
        documented_models=("CS-DL30-V100系列", "CS-Y3000F-V100系列"),
        whitelist_required=True,
        read_only=False,
        read_only_evidence="doc 775 declares a setter; writes are never replayed",
        limitations=(
            "documented only for CS-DL30-V100 series and CS-Y3000F-V100 series smart locks",
            "doc states a whitelist must be configured with EZVIZ or the call may fail",
            "only the optional Boolean 'enabled' field is accepted; the effect of an "
            "empty object is unverified",
            "doc 775 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_FACE_VOICE,
    ),
    _component_entry(
        _OP_FACE_VOICE_LOCK_GET,
        "812",
        documented_models=("CS-DL30-V100系列", "CS-Y3000F-V100系列"),
        whitelist_required=True,
        read_only=True,
        read_only_evidence="doc 812 declares the query function for the DoorLock/0 variant",
        limitations=(
            "documented only for CS-DL30-V100 series and CS-Y3000F-V100 series smart locks",
            "doc states a whitelist must be configured with EZVIZ or the call may fail",
            "DoorLock/0 path is a distinct documented variant; interchangeability "
            "with the Video/1 path is not established by reviewed sources, so "
            "the SDK never substitutes one for the other",
            "doc 812 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_NO_PAYLOAD,
    ),
    _component_entry(
        _OP_TEMPERATURE_GET,
        "1134",
        documented_models=("CS-T51C-BG",),
        whitelist_required=None,
        read_only=True,
        read_only_evidence="doc 1134 declares a temperature query",
        limitations=(
            "documented only for CS-T51C-BG; other models are not guaranteed usable",
            "returns the raw temperature value verbatim in the documented range [-100.0, 100000.0]",
            "doc 1134 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_NO_PAYLOAD,
    ),
    _component_entry(
        _OP_SMOKE_COUNTDOWN_GET,
        "1447",
        documented_models=("CS-T4C-BG",),
        whitelist_required=None,
        read_only=True,
        read_only_evidence="doc 1447 declares a power countdown info query",
        limitations=(
            "documented only for CS-T4C-BG fire detector; other models are not guaranteed usable",
            "doc 1447's return table defines deviceMeta code 0x00000000 as "
            "success; it does not state the layer is always present — its "
            "absence does not prove the device executed",
        ),
        payload_kind=_NO_PAYLOAD,
    ),
    _component_entry(
        _OP_GAS_COUNTDOWN_QUERY,
        "998",
        documented_models=("CS-T8C-DG",),
        whitelist_required=None,
        read_only=True,
        read_only_evidence=(
            "doc 998's opening line states the interface queries countdown "
            "information; the PUT method is the documented transport, not a "
            "write action"
        ),
        limitations=(
            "documented only for CS-T8C-DG; other models are not guaranteed usable",
            "payload must be explicit JSON null; a missing payload is rejected",
            "doc 998 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_EXPLICIT_NULL,
    ),
    _component_entry(
        _OP_CHIME_POWER_SET,
        "868",
        documented_models=("CS-CMT-CHIME-B",),
        whitelist_required=None,
        read_only=False,
        read_only_evidence="doc 868 declares a surplus-power setter; writes are never replayed",
        limitations=(
            "documented only for CS-CMT-CHIME-B; other models are not guaranteed usable",
            "payload is a bare non-bool integer in [0, 100]; the SDK does not "
            "interpret it as changing physical battery capacity and applies no "
            "value or unit conversion",
            "doc 868 shows deviceMeta only in a success example; whether it is "
            "always present is unstated — its absence does not prove the "
            "device executed",
        ),
        payload_kind=_CHIME_POWER,
    ),
    _Entry(
        ComponentInfo(
            operation_id="cn.components.hdd_format.invoke",
            source_doc_id="847",
            source_url="https://open.ys7.com/help/847",
            raw_path="/api/v3/otap/action/{deviceSerial}/Default/0/HDD/FormatSpecifyHDD",
            method="PUT",
            documented_models=("CS-CTQ1P-6E2WPFBS-B",),
            whitelist_required=None,
            read_only=False,
            read_only_evidence="doc 847 declares a local HDD format action",
            callable=False,
            limitations=(
                "destructive disk-format action without a documented disk-selection "
                "rule; kept as metadata only and refused before authentication",
            ),
        ),
        None,
        None,
    ),
    _Entry(
        ComponentInfo(
            operation_id="cn.components.human_detection.set",
            source_doc_id="3873",
            source_url="https://open.ys7.com/help/3873",
            # The raw template is kept verbatim from the doc: double braces
            # and a quoted index whose meaning is unconfirmed.
            raw_path='/api/v3/otap/prop/{{deviceSerial}}/global/["0"]/HumanDetection/InDetectEnabled',
            method="PUT",
            documented_models=(),
            whitelist_required=None,
            read_only=False,
            read_only_evidence="doc 3873 declares a human-detection enable setter",
            callable=False,
            limitations=(
                "the '{{deviceSerial}}' and '[\"0\"]' template spellings are "
                "unconfirmed and no model applicability is stated; kept as "
                "metadata only and refused before authentication",
            ),
        ),
        None,
        None,
    ),
)

_INDEX: dict[str, _Entry] = {entry.info.operation_id: entry for entry in _CATALOGUE}


def _lookup(operation_id: object) -> _Entry:
    if isinstance(operation_id, str):
        entry = _INDEX.get(operation_id)
        if entry is not None:
            return entry
    raise ValueError("unknown component operation_id")


def _render_body(entry: _Entry, payload: object) -> tuple[object, dict[str, str]]:
    """Resolve the documented body and headers before authentication.

    Returns ``(json_value, headers)``; ``json_value`` may be ``None`` for
    no-body operations (omitted on the wire) or ``JSON_NULL`` for the doc
    998 explicit-null body. Every callable doc — including the no-body
    GETs 774, 812, 1134, and 1447 — declares
    ``Content-Type: application/json`` in its parameter table and curl,
    so the header is sent even when no body travels.
    """
    headers = {"Content-Type": _JSON_CONTENT_TYPE}
    kind = entry.payload_kind
    if kind == _NO_PAYLOAD:
        if payload is not _UNSET:
            raise ValueError("operation accepts no request payload")
        return None, headers
    if payload is _UNSET:
        raise ValueError("operation requires an explicit payload")
    if kind == _EXPLICIT_NULL:
        if payload is not None:
            raise ValueError("operation requires payload=None (JSON null body)")
        return JSON_NULL, headers
    if kind == _FACE_VOICE:
        return _prepare_face_voice_body(payload), headers
    if kind == _CHIME_POWER:
        return _prepare_chime_power_body(payload), headers
    raise ValueError("operation is not callable")  # pragma: no cover


class Components:
    """Synchronous facade over the reviewed component-path catalogue."""

    __slots__ = ("_client",)

    def __init__(self, client: Client) -> None:
        self._client = client

    def describe(self, operation_id: str) -> ComponentInfo:
        """Return the documentation record for ``operation_id`` (no I/O)."""
        return _lookup(operation_id).info

    def list_documented(self) -> tuple[ComponentInfo, ...]:
        """Return every documented entry, callable or not (no I/O)."""
        return tuple(entry.info for entry in _CATALOGUE)

    def call(
        self,
        operation_id: str,
        device_serial: str,
        *,
        payload: object = _UNSET,
    ) -> ApiResponse:
        """Invoke a callable catalogue entry for one device serial.

        ``payload`` must be omitted for no-body operations, ``None`` for
        the doc 998 explicit-null query, a mapping for doc 775, and an
        integer for doc 868. Non-callable and unknown ids raise a fixed
        ``ValueError`` before authentication.
        """
        with _diagnostic_scope(self._client, entrypoint="components.call", mode="sync"):
            entry = _lookup(operation_id)
            if entry.operation is None:
                raise ValueError("component operation is documented but not callable")
            body, headers = _render_body(entry, payload)
            if body is None:
                return self._client.request(
                    entry.operation,
                    path_params={"deviceSerial": device_serial},
                    headers=headers,
                )
            return self._client.request(
                entry.operation,
                path_params={"deviceSerial": device_serial},
                json=body,
                headers=headers,
            )


class AsyncComponents:
    """Asynchronous facade over the reviewed component-path catalogue."""

    __slots__ = ("_client",)

    def __init__(self, client: AsyncClient) -> None:
        self._client = client

    def describe(self, operation_id: str) -> ComponentInfo:
        """Return the documentation record for ``operation_id`` (no I/O)."""
        return _lookup(operation_id).info

    def list_documented(self) -> tuple[ComponentInfo, ...]:
        """Return every documented entry, callable or not (no I/O)."""
        return tuple(entry.info for entry in _CATALOGUE)

    async def call(
        self,
        operation_id: str,
        device_serial: str,
        *,
        payload: object = _UNSET,
    ) -> ApiResponse:
        """Async variant of :meth:`Components.call`."""
        with _diagnostic_scope(self._client, entrypoint="components.call", mode="async"):
            entry = _lookup(operation_id)
            if entry.operation is None:
                raise ValueError("component operation is documented but not callable")
            body, headers = _render_body(entry, payload)
            if body is None:
                return await self._client.request(
                    entry.operation,
                    path_params={"deviceSerial": device_serial},
                    headers=headers,
                )
            return await self._client.request(
                entry.operation,
                path_params={"deviceSerial": device_serial},
                json=body,
                headers=headers,
            )
