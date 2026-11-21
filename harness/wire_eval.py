"""Offline transport-contract conformance evaluator for the unified EZVIZ SDK.

Drives the SDK exclusively through ``httpx.MockTransport`` with synthetic
credentials and synthetic responses, then compares emitted HTTP requests and
decoded responses against the reviewed transport contracts. The outer
harness (an isolated subprocess CLI written separately) provides process
isolation and network/output enforcement; this module only measures
conformance.

Hard rules honored here:

* the SDK is never imported at module import time, never executed through
  ``eval``/``exec``, and no test module is run;
* every request is served by ``httpx.MockTransport`` -- no environment reads,
  no real network access;
* expectations derive only from contract/binding/case inputs, never from SDK
  output (``decode_response`` is not used to build expectations);
* failures are reported only as fixed problem-code strings -- never as
  exception text, request values, or response values.

Public API:
    evaluate(contracts, bindings, cases, repo_root) -> report dict
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import math
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_from_bytes, urlsplit

import httpx

__all__ = ["evaluate"]

_SCHEMA_VERSION = 1
_EVIDENCE = "offline_contract_conformance_only"

_CONTEXT = {
    "app_key": "EVAL_APP_KEY",
    "app_secret": "EVAL_APP_SECRET",
    "device_serial": "EV1234567",
    "other_serial": "EV7654321",
    "account_id": "EVAL_ACCOUNT_ID",
    "account_name": "eval_account",
    "password_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "old_password": "OLD_PASSWORD",
    "new_password": "NEW_PASSWORD",
    "verification_code": "ABCDEF",
    "request_id": "EVAL_REQUEST_ID",
    "url_id": "EVAL_URL_ID",
    "ssid": "EVAL_SSID",
}

_API_ORIGIN = {
    "domestic": "https://open.ys7.com",
    "overseas": "https://isgpopen.ezvizlife.com",
}

_DOMESTIC = "domestic"
_AUTH_OPERATION_IDS = frozenset({"cn.token.get", "global.token.get"})
_AUTH_OPERATION_BY_REGION = {"domestic": "cn.token.get", "overseas": "global.token.get"}
_TOKEN_FIELD = "accessToken"
_CLOCK = lambda: 1000.0  # noqa: E731 - fixed synthetic clock
_REFRESH_SKEW = 0.0
_AUTH_EXPIRE_MS = 2000123
_TOKEN_EXPIRES_AT = 2000.123
_MESSAGE_OK = "contract-eval-ok"
_CURSOR_LIST_OPERATION = "cn.devices.list_by_id"
_REDACTED_MESSAGE_OPERATION_IDS = frozenset(
    {
        "cn.device_add_links.create",
        "cn.device_add_links.records",
        "cn.device_add_links.list",
        "cn.settings.get_motion_area",
        "cn.settings.set_motion_area",
        "global.settings.set_motion_area",
        "cn.settings.get_motion_sensitivity",
        "cn.settings.set_motion_sensitivity",
        "cn.settings.get_detection_sensitivity",
        "cn.settings.set_detection_sensitivity",
        "cn.third_party_protocols.list_gb_licenses",
        "cn.settings.set_motion_tracking",
        "cn.settings.get_motion_tracking",
        "cn.settings.get_human_tracking",
        "cn.settings.set_human_tracking",
        "cn.settings.get_intelligent_detection_switch",
        "cn.settings.set_intelligent_detection_switch",
        "cn.settings.select_alarm_detection_mode",
        "cn.settings.get_advanced_alarm_detection_type",
        "cn.settings.get_alarm_detection_mode",
        "cn.settings.get_channel_light_linkage",
        "global.detectors.rename",
        "cn.settings.get_working_mode_plans",
        "cn.settings.set_huashu_4g_working_mode_plan",
        "cn.recordings.by_time",
        "cn.recordings.by_text_time",
        "cn.video.get_video_codec",
        _CURSOR_LIST_OPERATION,
    }
)
_MESSAGE_FAULT = "contract-eval-fault"
_EVAL_MARKER = "_eval_marker"
_EVAL_MARKER_VALUE = "contract-eval"
_AUTH_FAULT = "auth_fault"
_PERMISSION_FAULT = "permission_fault"
_HTTP_FAULT = "http_fault"
_SUCCESS = "success"
_SCENARIOS = (_SUCCESS, _AUTH_FAULT, _PERMISSION_FAULT, _HTTP_FAULT)
_REFRESH_OPERATION = "global.oauth.refresh_token"
_REFRESH_SCENARIOS = (_SUCCESS, "refresh_expired", "binding_mismatch", _HTTP_FAULT)
_MODES = ("sync", "async")

_FORMAT_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_DTO_WHITELIST = frozenset(
    {
        "AlarmQuery",
        "ArmingSchedule",
        "CloudRecordingDevice",
        "CruisePeriod",
        "PlaybackOptions",
        "PreviewEncoding",
        "StorageOrderOptions",
        "StorageFileQuery",
        "RamStatement",
    }
)
_BASE_HEADERS = frozenset(
    {
        "host",
        "accept",
        "accept-encoding",
        "connection",
        "user-agent",
        "content-length",
        "content-type",
    }
)
_PARAM_LOCATIONS = frozenset({"body", "query", "header", "path"})
_TOKEN_LOCATIONS = frozenset({"form", "query", "header", "json", "none"})
_BODY_ENCODINGS = frozenset({"form_urlencoded", "json", "unknown"})
_CASE_KEYS = frozenset(
    {
        "operation_id",
        "args",
        "kwargs",
        "fields",
        "data",
        "json_fields",
        "json_body",
        "path_fields",
        "response_page",
    }
)
_BODY_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_TEMPLATE_SEGMENT = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")
# Hex triplets producing ASCII bytes only; ``%80`` and above stay verbatim
# so an encoded non-ASCII byte sequence is never decoded as navigation.
_ASCII_PERCENT_TRIPLET = re.compile(r"%[0-7][0-9A-Fa-f]")
_CONTENT_TYPE = "content-type"
_CONTENT_TYPE_FOR_ENCODING = {
    "form_urlencoded": "application/x-www-form-urlencoded",
    "json": "application/json",
}


class _Reject(Exception):
    """Internal rejection carrying one fixed problem code."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


def _reject_duplicates(pairs: list) -> dict:
    """``object_pairs_hook`` rejecting duplicate object keys outright."""
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate object key")
        out[key] = value
    return out


def _strict_json_loads(text: str) -> object:
    return json.loads(text, object_pairs_hook=_reject_duplicates)


# ---------------------------------------------------------------------------
# Structural / value helpers
# ---------------------------------------------------------------------------


def _strict_equal(left: object, right: object) -> bool:
    """JSON-aware equality preserving the bool/int distinction."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        if set(left.keys()) != set(right.keys()):
            return False
        return all(_strict_equal(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(_strict_equal(a, b) for a, b in zip(left, right))
    return bool(left == right)


def _wire_str(value: object) -> str:
    """Render an expected scalar the way url-encoding transports render it."""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if value is None:
        return ""
    raise _Reject("case_invalid")


def _check_json_tree(value: object) -> None:
    """Require ``value`` to be a JSON-safe tree of finite scalars.

    Validates a decoded ``case.json_body``: every object key must be a
    string, arrays/objects recurse, and scalar leaves are limited to
    str/bool/int/finite-float/None. Anything else (NaN, Infinity, decoded
    datetime/DTO tags, non-string keys) is not representable on the wire
    and is rejected as ``case_invalid``.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _Reject("case_invalid")
            _check_json_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _check_json_tree(item)
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _Reject("case_invalid")
        return
    raise _Reject("case_invalid")


def _json_root_matches(value: object, declared: object) -> bool:
    """Whether ``value`` is a legal JSON instance of the declared root type.

    ``integer`` accepts ints only (never bools), ``number`` accepts ints and
    floats (never bools), ``null`` accepts exactly ``None``.
    """
    if declared == "object":
        return isinstance(value, dict)
    if declared == "array":
        return isinstance(value, list)
    if declared == "string":
        return isinstance(value, str)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "null":
        return value is None
    raise _Reject("contract_invalid")


def _is_dot_segment(segment: str) -> bool:
    """True when ``segment`` spells ``.``/``..``, even percent-encoded.

    ASCII-producing hex triplets decode case-insensitively; triplets at or
    above ``0x80`` stay verbatim.
    """
    return _ASCII_PERCENT_TRIPLET.sub(lambda match: chr(int(match.group(0)[1:], 16)), segment) in (
        ".",
        "..",
    )


def _template_names(path: str) -> list:
    """Ordered placeholder names of a validated contract path template.

    Only complete-segment ``{name}`` placeholders count; any other brace
    usage (including ``}x{`` reversals), an embedded expression, and a
    static dot segment — plain or percent-encoded — is a contract defect
    rejected by ``_check_contract``.
    """
    names = []
    for segment in path.split("/"):
        match = _TEMPLATE_SEGMENT.match(segment)
        if match:
            names.append(match.group(1))
        elif "{" in segment or "}" in segment:
            raise _Reject("contract_invalid")
        elif _is_dot_segment(segment):
            raise _Reject("contract_invalid")
    return names


def _encode_path_segment(value: object) -> str:
    """Percent-encode one raw path-segment value independently of the SDK.

    The case value must decode to a non-blank ``str``: an empty or
    all-whitespace value is rejected, while leading/trailing whitespace on
    an otherwise non-blank value is preserved and encoded. Raw ``/`` and
    ``\\`` are rejected outright — a path separator is never legal data —
    as is every Unicode Cc control character and a lone ``.``/``..``. The
    survivor is UTF-8 encoded and percent-escaped exactly once with an
    empty safe set, so a literal ``%`` becomes data (``a%2Fb`` encodes to
    ``a%252Fb``), never a second decoding layer. Rejections are
    ``case_invalid`` and never carry the offending value or a chained
    encoding exception.
    """
    if not isinstance(value, str):
        raise _Reject("case_invalid")
    if not value.strip() or value in (".", ".."):
        raise _Reject("case_invalid")
    if any(unicodedata.category(ch) == "Cc" or ch in "/\\" for ch in value):
        raise _Reject("case_invalid")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        raw = None
    if raw is None:
        # A lone surrogate cannot be UTF-8 encoded. The ``_Reject`` is raised
        # outside the ``except`` block so the ``UnicodeEncodeError`` (whose
        # ``.object`` embeds the raw value) lands on neither ``__context__``
        # nor ``__cause__`` — the same principle the SDK applies.
        raise _Reject("case_invalid")
    return quote_from_bytes(raw, safe="")


def _render_expected_path(template: str, path_fields: dict) -> str:
    """Render the expected wire path from the contract template and case
    values only — never from SDK code or the observed request."""
    out = []
    for segment in template.split("/"):
        match = _TEMPLATE_SEGMENT.match(segment)
        if match is None:
            out.append(segment)
            continue
        name = match.group(1)
        if name not in path_fields:
            raise _Reject("case_invalid")
        out.append(_encode_path_segment(path_fields[name]))
    return "/".join(out)


def _set_path(root: dict, dotted: str, value: object) -> None:
    node = root
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _build_payload(
    success: dict,
    layout: str,
    code: object,
    message: str,
    data: object,
    *,
    response_page: dict | None = None,
) -> dict:
    envelope: dict = {}
    _set_path(envelope, success["code_path"], code)
    _set_path(envelope, success["message_path"], message)
    _set_path(envelope, success["data_path"], data)
    if response_page is not None:
        envelope["page"] = response_page
    envelope[_EVAL_MARKER] = _EVAL_MARKER_VALUE
    if not layout:
        return envelope
    root: dict = {}
    _set_path(root, layout, envelope)
    return root


# ---------------------------------------------------------------------------
# Placeholder / tag decoding
# ---------------------------------------------------------------------------


def _decode_ref(name: object, context: dict) -> object:
    if not isinstance(name, str) or name not in context:
        raise _Reject("case_invalid")
    return context[name]


def _decode_format(template: object, context: dict) -> str:
    if not isinstance(template, str):
        raise _Reject("case_invalid")
    out: list[str] = []
    pos = 0
    for match in _FORMAT_RE.finditer(template):
        literal = template[pos : match.start()]
        if "{" in literal or "}" in literal:
            raise _Reject("case_invalid")
        name = match.group(1)
        if name not in context:
            raise _Reject("case_invalid")
        out.append(literal)
        out.append(str(context[name]))
        pos = match.end()
    tail = template[pos:]
    if "{" in tail or "}" in tail:
        raise _Reject("case_invalid")
    out.append(tail)
    return "".join(out)


def _decode_tag(value: dict, context: dict, dto_registry: dict) -> object:
    tag = value.get("$type")
    if tag == "datetime":
        if set(value.keys()) != {"$type", "value"}:
            raise _Reject("case_invalid")
        raw = value["value"]
        if not isinstance(raw, str):
            raise _Reject("case_invalid")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            raise _Reject("case_invalid") from None
    if not isinstance(tag, str) or tag not in _DTO_WHITELIST:
        raise _Reject("case_invalid")
    if not set(value.keys()) <= {"$type", "args", "kwargs"}:
        raise _Reject("case_invalid")
    cls = dto_registry.get(tag)
    if cls is None:
        raise _Reject("case_invalid")
    args = value.get("args", [])
    kwargs = value.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise _Reject("case_invalid")
    decoded_args = [_decode_value(item, context, dto_registry) for item in args]
    decoded_kwargs = {
        key: _decode_value(item, context, dto_registry) for key, item in kwargs.items()
    }
    try:
        return cls(*decoded_args, **decoded_kwargs)
    except Exception:
        raise _Reject("case_invalid") from None


def _decode_value(value: object, context: dict, dto_registry: dict) -> object:
    if isinstance(value, dict):
        keys = set(value.keys())
        if keys == {"$ref"}:
            return _decode_ref(value["$ref"], context)
        if keys == {"$format"}:
            return _decode_format(value["$format"], context)
        if "$type" in value:
            return _decode_tag(value, context, dto_registry)
        return {key: _decode_value(item, context, dto_registry) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_value(item, context, dto_registry) for item in value]
    return value


# ---------------------------------------------------------------------------
# SDK import (deferred, location-verified; never purges pre-loaded modules so
# harness/test fault injection into already-imported SDK objects stays live)
# ---------------------------------------------------------------------------


def _import_sdk(repo_root: Path):
    """Import ``ezviz_unified`` and verify every SDK module comes from the repo."""
    try:
        package_dir = (Path(repo_root) / "src" / "ezviz_unified").resolve()
    except (OSError, TypeError):
        return None, "sdk_import_failed"
    if not package_dir.is_dir():
        return None, "sdk_import_failed"
    try:
        package = importlib.import_module("ezviz_unified")
    except Exception:
        return None, "sdk_import_failed"
    for name, module in list(sys.modules.items()):
        if name != "ezviz_unified" and not name.startswith("ezviz_unified."):
            continue
        module_file = getattr(module, "__file__", None)
        if not isinstance(module_file, str):
            return None, "sdk_import_failed"
        try:
            if not Path(module_file).resolve().is_relative_to(package_dir):
                return None, "sdk_import_failed"
        except OSError:
            return None, "sdk_import_failed"
    return package, None


# ---------------------------------------------------------------------------
# Contract / binding / case normalization
# ---------------------------------------------------------------------------


def _contract_index(contracts: dict) -> dict:
    out = {}
    entries = contracts.get("contracts") if isinstance(contracts, dict) else None
    for entry in entries or ():
        if isinstance(entry, dict) and isinstance(entry.get("operation_id"), str):
            out[entry["operation_id"]] = entry
    return out


def _binding_index(bindings: dict) -> dict:
    out = {}
    entries = bindings.get("bindings") if isinstance(bindings, dict) else None
    for entry in entries or ():
        if isinstance(entry, dict) and isinstance(entry.get("operation_id"), str):
            out[entry["operation_id"]] = entry
    return out


def _check_contract(contract: dict) -> None:
    if not isinstance(contract.get("method"), str):
        raise _Reject("contract_invalid")
    if not isinstance(contract.get("path"), str):
        raise _Reject("contract_invalid")
    if contract.get("region") not in (_DOMESTIC, "overseas"):
        raise _Reject("contract_invalid")
    if contract.get("token_location") not in _TOKEN_LOCATIONS:
        raise _Reject("contract_invalid")
    if contract.get("body_encoding") not in _BODY_ENCODINGS:
        raise _Reject("contract_invalid")
    if not isinstance(contract.get("read_only"), bool):
        raise _Reject("contract_invalid")
    if not isinstance(contract.get("parameters"), list):
        raise _Reject("contract_invalid")
    success = contract.get("success")
    if not isinstance(success, dict):
        raise _Reject("contract_invalid")
    for key in ("code_path", "message_path", "data_path"):
        if not isinstance(success.get(key), str) or not success[key]:
            raise _Reject("contract_invalid")
    values = success.get("values")
    if (
        not isinstance(values, list)
        or not values
        or any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in values)
    ):
        raise _Reject("contract_invalid")
    envelope_paths = success.get("envelope_paths", [""])
    if (
        not isinstance(envelope_paths, list)
        or not envelope_paths
        or any(not isinstance(p, str) for p in envelope_paths)
    ):
        raise _Reject("contract_invalid")
    if "request_body" in contract:
        spec = contract["request_body"]
        if not isinstance(spec, dict) or set(spec) - {
            "type",
            "required",
            "required_uncertainty",
            "evidence",
        }:
            raise _Reject("contract_invalid")
        if not isinstance(spec.get("type"), str) or spec["type"] not in _BODY_TYPES:
            raise _Reject("contract_invalid")
        if "required" not in spec:
            raise _Reject("contract_invalid")
        required = spec["required"]
        if required is None:
            uncertainty = spec.get("required_uncertainty")
            if not isinstance(uncertainty, str) or not uncertainty.strip():
                raise _Reject("contract_invalid")
        elif type(required) is not bool:
            raise _Reject("contract_invalid")
        if not isinstance(spec.get("evidence"), str) or not spec["evidence"].strip():
            raise _Reject("contract_invalid")
        if contract["body_encoding"] != "json":
            raise _Reject("contract_invalid")
        if contract["token_location"] not in ("none", "query", "header"):
            raise _Reject("contract_invalid")
        if any(
            isinstance(param, dict) and param.get("location") == "body"
            for param in contract["parameters"]
        ):
            raise _Reject("contract_invalid")
    _check_path_template(contract)


def _check_path_template(contract: dict) -> None:
    """Path template placeholders are structural: every ``{name}`` segment
    must be backed by a required ``location=path`` parameter and vice versa;
    the managed ``accessToken`` is never a legal path parameter."""
    path = contract.get("path")
    parameters = contract.get("parameters")
    if not isinstance(path, str) or not isinstance(parameters, list):
        raise _Reject("contract_invalid")
    names = _template_names(path)
    path_params = [
        param for param in parameters if isinstance(param, dict) and param.get("location") == "path"
    ]
    if any(param.get("name") == _TOKEN_FIELD for param in path_params):
        raise _Reject("contract_invalid")
    if any(param.get("required") is not True for param in path_params):
        raise _Reject("contract_invalid")
    if {param.get("name") for param in path_params} != set(names):
        raise _Reject("contract_invalid")


def _check_auth_contract(contract: object) -> dict:
    """The auth contract is mandatory: no guessed method/path/origin defaults."""
    if not isinstance(contract, dict):
        raise _Reject("auth_contract_missing")
    _check_contract(contract)
    if not isinstance(contract.get("base_url"), str) or not contract["base_url"]:
        raise _Reject("auth_contract_missing")
    return contract


def _contract_layouts(contract: dict) -> list:
    success = contract.get("success")
    if not isinstance(success, dict):
        return [""]
    paths = success.get("envelope_paths")
    if isinstance(paths, list) and paths and all(isinstance(p, str) for p in paths):
        return list(paths)
    return [""]


def _parameter_maps(contract: dict):
    """Return ``(locations, constants, required, ct_specs)`` for the params.

    ``accessToken`` is managed by the client and path parameters are
    verified through the rendered path, so neither counts as a business
    field. Header ``Content-Type`` parameters are collected into
    ``ct_specs`` — a list of ``(required, has_constant, constant)`` — and
    checked against the wire Content-Type independently of the body
    encoding, so a documented mandatory ``application/json`` header is
    verified even when no body travels.
    """
    locations: dict[str, set] = {}
    constants: dict[str, tuple] = {}
    required: set[str] = set()
    ct_specs: list[tuple[bool, bool, object]] = []
    for param in contract["parameters"]:
        if not isinstance(param, dict):
            raise _Reject("contract_invalid")
        name = param.get("name")
        location = param.get("location")
        if not isinstance(name, str) or location not in _PARAM_LOCATIONS:
            raise _Reject("contract_invalid")
        if name == _TOKEN_FIELD:
            continue
        if location == "path":
            # Path parameters are verified through the rendered path, never
            # through the field/header/query buckets.
            continue
        if name.lower() == _CONTENT_TYPE and location == "header":
            ct_specs.append(
                (param.get("required") is True, "constant" in param, param.get("constant"))
            )
            continue
        locations.setdefault(name, set()).add(location)
        if "constant" in param:
            constants[name] = (location, param["constant"])
        if param.get("required") is True:
            required.add(name)
    return locations, constants, required, ct_specs


def _context_for(contract: dict) -> dict:
    context = dict(_CONTEXT)
    context["api_origin"] = _API_ORIGIN[contract["region"]]
    return context


def _context_for_op(raw_case: object, contract_map: dict) -> dict:
    if isinstance(raw_case, dict) and isinstance(raw_case.get("operation_id"), str):
        contract = contract_map.get(raw_case["operation_id"])
        if isinstance(contract, dict) and contract.get("region") in _API_ORIGIN:
            return _context_for(contract)
    context = dict(_CONTEXT)
    context["api_origin"] = _API_ORIGIN[_DOMESTIC]
    return context


def _normalize_case(raw: object, contract_map: dict, context: dict, dto_registry: dict) -> dict:
    if not isinstance(raw, dict):
        raise _Reject("case_invalid")
    if set(raw.keys()) - _CASE_KEYS:
        raise _Reject("case_invalid")
    operation_id = raw.get("operation_id")
    if not isinstance(operation_id, str) or operation_id not in contract_map:
        raise _Reject("unknown_operation_id")
    if ("response_page" in raw) != (operation_id == _CURSOR_LIST_OPERATION):
        raise _Reject("case_invalid")
    args = raw.get("args", [])
    kwargs = raw.get("kwargs", {})
    fields = raw.get("fields", {})
    path_fields = raw.get("path_fields", {})
    json_fields = raw.get("json_fields", [])
    if not isinstance(args, list):
        raise _Reject("case_invalid")
    if not isinstance(kwargs, dict) or any(not isinstance(k, str) for k in kwargs):
        raise _Reject("case_invalid")
    if not isinstance(fields, dict) or any(not isinstance(k, str) for k in fields):
        raise _Reject("case_invalid")
    if not isinstance(path_fields, dict) or any(not isinstance(k, str) for k in path_fields):
        raise _Reject("case_invalid")
    if (
        not isinstance(json_fields, list)
        or any(not isinstance(name, str) for name in json_fields)
        or len(set(json_fields)) != len(json_fields)
    ):
        raise _Reject("case_invalid")
    if _TOKEN_FIELD in fields:
        raise _Reject("managed_token_field")
    contract = contract_map[operation_id]
    whole_body = "request_body" in contract
    if ("json_body" in raw) != whole_body:
        # Whole-body contracts require an explicitly present json_body (a
        # null value is distinct from an absent key); every other contract
        # forbids the key outright.
        raise _Reject("case_invalid")
    decoded = {
        "operation_id": operation_id,
        "contract": contract,
        "args": [_decode_value(item, context, dto_registry) for item in args],
        "kwargs": {key: _decode_value(item, context, dto_registry) for key, item in kwargs.items()},
        "fields": {key: _decode_value(item, context, dto_registry) for key, item in fields.items()},
        "path_fields": {
            key: _decode_value(item, context, dto_registry) for key, item in path_fields.items()
        },
        "json_fields": list(json_fields),
        "json_body": (
            _decode_value(raw["json_body"], context, dto_registry) if whole_body else None
        ),
        "data": _decode_value(raw.get("data"), context, dto_registry),
    }
    if "response_page" in raw:
        response_page = _decode_value(raw["response_page"], context, dto_registry)
        if not isinstance(response_page, dict) or "nextId" not in response_page:
            raise _Reject("case_invalid")
        next_id = response_page["nextId"]
        if next_id is not None and not isinstance(next_id, str):
            raise _Reject("case_invalid")
        for key in ("total", "size"):
            if key in response_page and (
                not isinstance(response_page[key], int) or isinstance(response_page[key], bool)
            ):
                raise _Reject("case_invalid")
        decoded["response_page"] = response_page
    # Contract-side template consistency is re-checked here so a structural
    # defect reports contract_invalid rather than a misleading case_invalid.
    _check_path_template(contract)
    # The declared template placeholders are the only legal path_fields keys;
    # missing or extra keys are case defects, never silently ignored. This
    # also validates that every supplied value encodes safely.
    placeholders = set(_template_names(contract["path"]))
    if set(decoded["path_fields"]) != placeholders:
        raise _Reject("case_invalid")
    decoded["expected_path"] = _render_expected_path(contract["path"], decoded["path_fields"])
    if whole_body:
        json_body = decoded["json_body"]
        # The declared request_body.type fixes the JSON root shape;
        # _check_json_tree enforces finite scalars and string keys
        # recursively, and rejects non-JSON decoded values.
        spec = contract["request_body"]
        if not isinstance(spec, dict):
            raise _Reject("contract_invalid")
        if not _json_root_matches(json_body, spec.get("type")):
            raise _Reject("case_invalid")
        _check_json_tree(json_body)
        if isinstance(json_body, dict) and any(
            key.lower() == _TOKEN_FIELD.lower() for key in json_body
        ):
            # The managed credential must never be smuggled into the payload
            # top level; nested keys remain ordinary product attributes.
            raise _Reject("managed_token_field")
    locations, constants, required, ct_specs = _parameter_maps(contract)
    if any(
        has_constant and not isinstance(constant, str) for _, has_constant, constant in ct_specs
    ):
        # A non-string Content-Type constant could never be compared as a
        # header value; this is a contract authoring defect.
        raise _Reject("contract_invalid")
    decoded["ct_specs"] = ct_specs
    if operation_id not in _AUTH_OPERATION_IDS:
        # The two developer auth operations take no business fields at all;
        # appKey/appSecret are verified on the wire from synthetic context.
        for name in sorted(required):
            if name not in constants and name not in decoded["fields"]:
                raise _Reject("missing_required_field")
        for name in decoded["fields"]:
            where = locations.get(name)
            if not where:
                raise _Reject("unknown_field")
            if len(where) > 1:
                raise _Reject("ambiguous_field_location")
        for name in decoded["json_fields"]:
            if name not in decoded["fields"]:
                raise _Reject("case_invalid")
            where = locations.get(name)
            if not where or len(where) != 1 or next(iter(where)) != "body":
                raise _Reject("case_invalid")
    else:
        if decoded["fields"] or decoded["json_fields"]:
            raise _Reject("case_invalid")
    return decoded


# ---------------------------------------------------------------------------
# Expected-wire derivation
# ---------------------------------------------------------------------------


def _expected_wire(
    contract: dict,
    fields: dict,
    json_fields: list,
    token: str,
    json_body: object = None,
) -> dict:
    """Map expected wire values to header/query/form/json buckets.

    Header-bucket keys are all lowercase (including the managed token);
    ``Content-Type`` is never in the header bucket -- it is verified through
    the declared body encoding plus any contract Content-Type parameter
    specs checked separately in ``_check_business_request``. For contracts
    declaring ``request_body`` the json bucket is the complete body object
    rather than a per-field map, and named body fields can never appear
    alongside it.
    """
    encoding = contract["body_encoding"]
    token_location = contract["token_location"]
    locations, constants, _required, _ct_specs = _parameter_maps(contract)
    json_names = set(json_fields)
    whole_body = "request_body" in contract
    expected = {
        "header": {},
        "query": {},
        "form": {},
        "json": {},
        "json_fields": json_names,
        "has_body": False,
    }
    if whole_body:
        expected["json"] = json_body
        expected["has_body"] = True

    def place(name: str, value: object, location: str) -> None:
        if location == "header":
            expected["header"][name.lower()] = _wire_str(value)
        elif location == "query":
            expected["query"][name] = [_wire_str(value)]
        elif location == "body":
            if whole_body:
                raise _Reject("contract_invalid")
            if encoding == "form_urlencoded":
                if name in json_names:
                    # JSON-structured form value: keep the decoded structure or
                    # JSON text; compared structurally, never _wire_str'd.
                    expected["form"][name] = [value]
                else:
                    expected["form"][name] = [_wire_str(value)]
            elif encoding == "json":
                expected["has_body"] = True
                expected["json"][name] = value
            else:
                raise _Reject("contract_invalid")
        else:
            raise _Reject("contract_invalid")

    for name, value in fields.items():
        where = locations.get(name)
        if not where:
            raise _Reject("unknown_field")
        if len(where) != 1:
            raise _Reject("ambiguous_field_location")
        place(name, value, next(iter(where)))
    for name, (location, constant) in constants.items():
        if name in fields:
            continue
        place(name, constant, location)
    if token_location == "form":
        expected["form"][_TOKEN_FIELD] = [token]
        expected["has_body"] = True
    elif token_location == "query":
        expected["query"][_TOKEN_FIELD] = [token]
    elif token_location == "header":
        expected["header"][_TOKEN_FIELD.lower()] = token
    elif token_location == "json":
        if whole_body:
            # A JSON-embedded token cannot share a caller-owned whole body;
            # reject explicitly instead of silently overwriting a key.
            raise _Reject("contract_invalid")
        expected["json"][_TOKEN_FIELD] = token
        expected["has_body"] = True
    elif token_location != "none":
        raise _Reject("contract_invalid")
    for name in json_names:
        if name not in expected["form"]:
            raise _Reject("case_invalid")
    return expected


# ---------------------------------------------------------------------------
# Request verification
# ---------------------------------------------------------------------------


def _request_origin(request: httpx.Request) -> str | None:
    parts = urlsplit(str(request.url))
    if parts.scheme != "https" or parts.hostname is None:
        return None
    port = parts.port
    if port not in (None, 443):
        return f"https://{parts.hostname}:{port}"
    return f"https://{parts.hostname}"


def _request_form(request: httpx.Request) -> dict:
    return parse_qs(request.content.decode("utf-8"), keep_blank_values=True)


def _business_headers(request: httpx.Request) -> dict | None:
    """Lower-cased business headers; ``None`` when duplicates or a forged Host
    appear. ``Content-Type`` is excluded -- it is verified via the encoding."""
    headers: dict = {}
    for raw_name, raw_value in request.headers.raw:
        name = raw_name.decode("latin-1").lower()
        if name in _BASE_HEADERS:
            continue
        value = raw_value.decode("latin-1")
        if name in headers:
            return None  # duplicated business header
        headers[name] = value
    return headers


def _host_ok(request: httpx.Request) -> bool:
    host = request.headers.get("host")
    if not host:
        return False
    parts = urlsplit(str(request.url))
    expected = parts.hostname or ""
    port = parts.port
    if port not in (None, 443):
        expected = f"{expected}:{port}"
    return host.lower() == expected.lower()


def _check_auth_request(
    request: httpx.Request, auth_contract: dict, expected_origin: str
) -> str | None:
    if request.method != auth_contract["method"]:
        return "auth_request_mismatch"
    if _request_origin(request) != expected_origin:
        return "auth_request_mismatch"
    if not _host_ok(request):
        return "auth_request_mismatch"
    parts = urlsplit(str(request.url))
    if parts.path != auth_contract["path"]:
        return "auth_request_mismatch"
    if parse_qs(parts.query, keep_blank_values=True):
        return "auth_request_mismatch"
    if request.headers.get("content-type") != "application/x-www-form-urlencoded":
        return "auth_request_mismatch"
    expected_form = {
        "appKey": [_CONTEXT["app_key"]],
        "appSecret": [_CONTEXT["app_secret"]],
    }
    try:
        form = _request_form(request)
    except Exception:
        return "auth_request_mismatch"
    if form != expected_form:
        return "auth_request_mismatch"
    business = _business_headers(request)
    if business is None or business:
        return "auth_request_mismatch"
    return None


def _check_business_request(request: httpx.Request, contract: dict, expected: dict) -> str | None:
    encoding = contract["body_encoding"]
    if request.method != contract["method"]:
        return "request_mismatch"
    if _request_origin(request) != expected["origin"]:
        return "request_mismatch"
    if not _host_ok(request):
        return "request_mismatch"
    # raw_path is the encoded view: an encoded separator (%2F, %5C) stays
    # data and can never masquerade as a real path segment boundary. The
    # query part is stripped before comparing the byte path. The expected
    # path must be pure ASCII (percent-encoding only produces ASCII); a
    # non-ASCII static segment is a contract defect reported as
    # contract_invalid, never silently compared against the decoded view.
    raw_path = request.url.raw_path.split(b"?", 1)[0]
    # ``expected["path"]`` carries the independently rendered template path;
    # callers that check a fixed-path contract directly may omit it, in which
    # case the contract's own literal path is the expectation.
    expected_path = expected.get("path", contract["path"])
    try:
        expected_raw = expected_path.encode("ascii")
    except (UnicodeEncodeError, AttributeError):
        return "contract_invalid"
    if raw_path != expected_raw:
        return "request_mismatch"
    parts = urlsplit(str(request.url))
    if parse_qs(parts.query, keep_blank_values=True) != expected["query"]:
        return "request_mismatch"
    business = _business_headers(request)
    if business is None or business != expected["header"]:
        return "request_mismatch"
    # Contract-declared Content-Type header parameters are verified here —
    # missing when required, duplicated, or unequal to the declared constant
    # are all mismatches — independently of the body encoding so a documented
    # mandatory Content-Type is checked even when no body travels. Duplicate
    # raw occurrences are a mismatch regardless of requiredness. When the
    # contract declares no Content-Type parameter the body-encoding check
    # below governs exactly as before.
    try:
        raw_ct_values = request.headers.get_list(_CONTENT_TYPE)
    except Exception:
        return "request_mismatch"
    ct_specs = expected.get("ct_specs", ())
    if ct_specs:
        if not raw_ct_values:
            # Absent header: only a declared required spec rejects; optional
            # or unknown-requiredness specs tolerate the absence.
            if any(spec[0] for spec in ct_specs):
                return "request_mismatch"
        elif len(raw_ct_values) > 1:
            return "request_mismatch"
        else:
            # A present header must equal every declared constant — a wrong
            # value stays a mismatch regardless of requiredness. A spec
            # without a constant accepts any single present value.
            constants = {spec[2] for spec in ct_specs if spec[1]}
            if constants and raw_ct_values[0] not in constants:
                return "request_mismatch"
    elif len(raw_ct_values) > 1:
        return "request_mismatch"
    content_type = raw_ct_values[0] if raw_ct_values else None
    # With a declared Content-Type parameter the value was already verified
    # above against the declaration itself; the canonical default for the
    # body encoding must not impose a second, conflicting value. The
    # canonical rule stays in force only when no CT parameter is declared.
    ct_declared = bool(ct_specs)
    if encoding == "form_urlencoded":
        if not ct_declared and content_type != _CONTENT_TYPE_FOR_ENCODING["form_urlencoded"]:
            return "request_mismatch"
        try:
            form = _request_form(request)
        except Exception:
            return "request_mismatch"
        json_names = expected["json_fields"]
        for name in json_names:
            values = form.get(name)
            if not values or len(values) != 1:
                return "request_mismatch"
            expected_values = expected["form"].get(name)
            if not expected_values or len(expected_values) != 1:
                return "request_mismatch"
            try:
                actual_json = _strict_json_loads(values[0])
            except ValueError:
                return "request_mismatch"
            expected_raw = expected_values[0]
            if isinstance(expected_raw, str):
                try:
                    expected_json = _strict_json_loads(expected_raw)
                except ValueError:
                    return "request_mismatch"
            elif isinstance(expected_raw, (dict, list)):
                expected_json = expected_raw
            else:
                return "request_mismatch"
            if not _strict_equal(actual_json, expected_json):
                return "request_mismatch"
        remainder = {k: v for k, v in form.items() if k not in json_names}
        expected_remainder = {k: v for k, v in expected["form"].items() if k not in json_names}
        if remainder != expected_remainder:
            return "request_mismatch"
    elif encoding == "json":
        if not expected["has_body"]:
            if request.content not in (b"", ""):
                return "request_mismatch"
            if (
                not ct_declared
                and content_type is not None
                and content_type != _CONTENT_TYPE_FOR_ENCODING["json"]
            ):
                return "request_mismatch"
        else:
            if not ct_declared and content_type != _CONTENT_TYPE_FOR_ENCODING["json"]:
                return "request_mismatch"
            try:
                body = _strict_json_loads(request.content.decode("utf-8"))
            except Exception:
                return "request_mismatch"
            if not _strict_equal(body, expected["json"]):
                return "request_mismatch"
    else:
        if request.content not in (b"", ""):
            return "request_mismatch"
    return None


# ---------------------------------------------------------------------------
# Entry-point resolution
# ---------------------------------------------------------------------------


def _resolve_entry(client: object, public_entry: object) -> object:
    if not isinstance(public_entry, str) or not public_entry:
        raise _Reject("binding_invalid")
    target = client
    for segment in public_entry.split("."):
        if not segment.isidentifier() or segment.startswith("_"):
            raise _Reject("binding_invalid")
        try:
            target = getattr(target, segment)
        except AttributeError:
            raise _Reject("binding_invalid") from None
    if not callable(target):
        raise _Reject("binding_invalid")
    return target


def _region_for(package: object, contract: dict) -> object:
    region_cls = getattr(package, "RegionConfig", None)
    if region_cls is None:
        raise _Reject("sdk_import_failed")
    if contract["region"] == _DOMESTIC:
        return region_cls.china()
    if contract["token_location"] == "none" and contract["operation_id"] not in _AUTH_OPERATION_IDS:
        return region_cls.overseas(api_base_url=_API_ORIGIN[contract["region"]])
    return region_cls.overseas()


# ---------------------------------------------------------------------------
# Payloads, expected counts, result classification
# ---------------------------------------------------------------------------


def _fault_code(success_code: object, digits: str) -> object:
    return digits if isinstance(success_code, str) else int(digits)


def _operation_outcome(package: object) -> dict:
    return {
        "api_response": getattr(package, "ApiResponse", None),
        "auth_error": getattr(package, "AuthenticationError", None),
        "permission_error": getattr(package, "PermissionDeniedError", None),
        "api_error": getattr(package, "ApiError", None),
    }


def _expected_result_scenario(contract: dict, scenario: str) -> str:
    is_public = (
        contract["token_location"] == "none" and contract["operation_id"] not in _AUTH_OPERATION_IDS
    )
    if scenario == _SUCCESS:
        return "response"
    if scenario == _AUTH_FAULT and contract["read_only"] and not is_public:
        return "response"
    if scenario == _AUTH_FAULT:
        return "auth_error"
    if scenario == _PERMISSION_FAULT:
        return "permission_error"
    return "api_error"


def _expected_call_counts(contract: dict, scenario: str) -> tuple[int, int]:
    """Return ``(business_calls, auth_calls)`` for a normal operation."""
    if contract["token_location"] == "none" and contract["operation_id"] not in _AUTH_OPERATION_IDS:
        return 1, 0
    if scenario == _AUTH_FAULT and contract["read_only"]:
        return 2, 2
    return 1, 1


def _check_response(
    outcome: object,
    package: object,
    success_payload: dict,
    success_code: object,
    data: object,
    expected_message: str,
) -> str | None:
    api_response = _operation_outcome(package)["api_response"]
    if api_response is not None and not isinstance(outcome, api_response):
        return "response_mismatch"
    if getattr(outcome, "http_status", None) != 200:
        return "response_mismatch"
    if not _strict_equal(getattr(outcome, "code", None), success_code):
        return "response_mismatch"
    if not _strict_equal(getattr(outcome, "message", None), expected_message):
        return "response_mismatch"
    if not _strict_equal(getattr(outcome, "data", None), data):
        return "response_mismatch"
    if not _strict_equal(getattr(outcome, "raw", None), success_payload):
        return "response_mismatch"
    return None


def _check_exception(outcome: object, package: object, scenario: str) -> str | None:
    errors = _operation_outcome(package)
    want = {
        _AUTH_FAULT: errors["auth_error"],
        _PERMISSION_FAULT: errors["permission_error"],
        _HTTP_FAULT: errors["api_error"],
    }[scenario]
    if want is None or type(outcome) is not want:
        return "wrong_exception_type"
    if getattr(outcome, "http_status", None) != (500 if scenario == _HTTP_FAULT else 200):
        return "wrong_exception_status"
    if scenario == _AUTH_FAULT and getattr(outcome, "invalid_token", None) is not True:
        return "wrong_exception_type"
    return None


# ---------------------------------------------------------------------------
# Shared execution core: one async runner; sync/async selected by mode.
# ---------------------------------------------------------------------------


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


async def _close_instance(instance: object, mode: str) -> None:
    if mode == "sync":
        closer = getattr(instance, "close", None)
    else:
        closer = getattr(instance, "aclose", None)
    if closer is None:
        raise _Reject("cleanup_failed")
    result = closer()
    if inspect.isawaitable(result):
        await result


def _run_coro(coro) -> tuple[str, object]:
    """Run one async cell; classify the outcome; never leak exception text."""
    try:
        return asyncio.run(coro)
    except _Reject:
        raise
    except Exception:
        raise _Reject("harness_error") from None


def _auth_token_value(n: int) -> str:
    return f"at.EVAL_TOKEN_{n}"


def _auth_data(auth_contract: dict, api_origin: str, n: int) -> dict:
    data = {
        "accessToken": _auth_token_value(n),
        "expireTime": _AUTH_EXPIRE_MS,
    }
    if auth_contract["region"] == "overseas":
        data["areaDomain"] = api_origin
    return data


def _auth_ok_payload(auth_contract: dict, api_origin: str, n: int, layout: str = "") -> dict:
    """Auth success envelope from the auth contract's own success layout."""
    success = auth_contract["success"]
    return _build_payload(
        success,
        layout,
        success["values"][0],
        _MESSAGE_OK,
        _auth_data(auth_contract, api_origin, n),
    )


class _BoundedQueues:
    """Request capture with an immediate hard bound on extra requests."""

    def __init__(self, business_limit: int, auth_limit: int) -> None:
        self.auth_requests: list[httpx.Request] = []
        self.business_requests: list[httpx.Request] = []
        self._business_limit = business_limit
        self._auth_limit = auth_limit
        self.exceeded = False

    def record_auth(self, request: httpx.Request) -> None:
        if len(self.auth_requests) >= self._auth_limit:
            self.exceeded = True
            raise _Reject("request_limit_exceeded")
        self.auth_requests.append(request)

    def record_business(self, request: httpx.Request) -> None:
        if len(self.business_requests) >= self._business_limit:
            self.exceeded = True
            raise _Reject("request_limit_exceeded")
        self.business_requests.append(request)


async def _execute_cell(
    *,
    package: object,
    contract: dict,
    case: dict | None,
    binding: dict | None,
    layout: str,
    scenario: str,
    mode: str,
    auth_contract: dict,
) -> tuple[str, object, _BoundedQueues, list]:
    """Create the client/manager, run one cell, close deterministically.

    Returns ``(outcome_kind, outcome, queues, recorded)``; raises ``_Reject``
    for fixed problem codes including ``cleanup_failed``.
    """
    is_auth_op = contract["operation_id"] in _AUTH_OPERATION_IDS
    expected_business, expected_auth = (
        (0, 1) if is_auth_op else _expected_call_counts(contract, scenario)
    )
    queues = _BoundedQueues(expected_business, expected_auth)
    recorded: list = []
    api_origin = _API_ORIGIN[contract["region"]]
    success = contract["success"]
    success_code = success["values"][0]
    success_payload = _build_payload(
        success,
        layout,
        success_code,
        _MESSAGE_OK,
        None if case is None else case["data"],
        response_page=(
            case.get("response_page") if case is not None and scenario != _HTTP_FAULT else None
        ),
    )

    def fault_payload(digits: str) -> dict:
        return _build_payload(
            success, layout, _fault_code(success_code, digits), _MESSAGE_FAULT, None
        )

    def auth_handler(request: httpx.Request) -> httpx.Response:
        queues.record_auth(request)  # hard bound: raises on any extra request
        if is_auth_op and scenario == _AUTH_FAULT:
            return httpx.Response(200, json=fault_payload("10002"))
        if is_auth_op and scenario == _PERMISSION_FAULT:
            return httpx.Response(200, json=fault_payload("10031"))
        auth_layout = layout if is_auth_op else _contract_layouts(auth_contract)[0]
        payload = _auth_ok_payload(
            auth_contract, api_origin, len(queues.auth_requests), auth_layout
        )
        return httpx.Response(500 if is_auth_op and scenario == _HTTP_FAULT else 200, json=payload)

    def business_handler(request: httpx.Request) -> httpx.Response:
        queues.record_business(request)  # hard bound: raises on any extra request
        index = len(queues.business_requests) - 1
        if scenario == _AUTH_FAULT and index == 0:
            return httpx.Response(200, json=fault_payload("10002"))
        if scenario == _PERMISSION_FAULT:
            return httpx.Response(200, json=fault_payload("10031"))
        if scenario == _HTTP_FAULT:
            return httpx.Response(500, json=success_payload)
        return httpx.Response(200, json=success_payload)

    region = _region_for(package, contract)
    is_sync = mode == "sync"
    if is_auth_op:
        cls = getattr(package, "TokenManager" if is_sync else "AsyncTokenManager", None)
    else:
        cls = getattr(package, "Client" if is_sync else "AsyncClient", None)
    if cls is None:
        raise _Reject("sdk_import_failed")
    kwargs = {
        "region": region,
        "clock": _CLOCK,
        "refresh_skew": _REFRESH_SKEW,
        "trust_env": False,
    }
    if is_auth_op:
        kwargs["transport"] = httpx.MockTransport(auth_handler)
    else:
        kwargs["auth_transport"] = httpx.MockTransport(auth_handler)
        kwargs["transport_factory"] = lambda origin: httpx.MockTransport(business_handler)
    instance = cls(_CONTEXT["app_key"], _CONTEXT["app_secret"], **kwargs)
    try:
        if is_auth_op:
            entry = getattr(instance, "get_token", None)
            if not callable(entry):
                raise _Reject("sdk_import_failed")
            args: list = []
            call_kwargs: dict = {}
        else:
            original_request = instance.request

            if is_sync:

                def spy(operation, **kw):
                    recorded.append(operation)
                    return original_request(operation, **kw)

            else:

                async def spy(operation, **kw):
                    recorded.append(operation)
                    return await original_request(operation, **kw)

            instance.request = spy
            entry = _resolve_entry(instance, (binding or {}).get("public_entry"))
            args = case["args"]
            call_kwargs = case["kwargs"]
        try:
            result = entry(*args, **call_kwargs)
            result = await _maybe_await(result)
            outcome = ("response", result)
        except Exception as exc:  # noqa: BLE001 - classified, never rendered
            outcome = ("exception", exc)
    finally:
        try:
            await _close_instance(instance, mode)
        except _Reject:
            raise
        except Exception:
            raise _Reject("cleanup_failed") from None
    return outcome[0], outcome[1], queues, recorded


# ---------------------------------------------------------------------------
# Cell verification
# ---------------------------------------------------------------------------


def _verify_operation_run(
    *,
    package: object,
    contract: dict,
    case: dict,
    layout: str,
    scenario: str,
    auth_contract: dict,
    api_origin: str,
    recorded: list,
    queues: _BoundedQueues,
    outcome_kind: str,
    outcome: object,
) -> str | None:
    success = contract["success"]
    success_code = success["values"][0]
    success_payload = _build_payload(
        success,
        layout,
        success_code,
        _MESSAGE_OK,
        case["data"],
        response_page=case.get("response_page"),
    )
    if queues.exceeded:
        return "request_limit_exceeded"
    expected_origin = auth_contract["base_url"]
    for auth_request in queues.auth_requests:
        problem = _check_auth_request(auth_request, auth_contract, expected_origin)
        if problem is not None:
            return problem
    for index, business_request in enumerate(queues.business_requests):
        expected = _expected_wire(
            contract,
            case["fields"],
            case["json_fields"],
            _auth_token_value(index + 1),
            case["json_body"],
        )
        expected["origin"] = api_origin
        expected["path"] = case["expected_path"]
        expected["ct_specs"] = case["ct_specs"]
        problem = _check_business_request(business_request, contract, expected)
        if problem is not None:
            return problem
    if len(recorded) != 1:
        return "operation_dispatch"
    operation = recorded[0]
    if (
        getattr(operation, "method", None) != contract["method"]
        or getattr(operation, "path", None) != contract["path"]
        or getattr(operation, "token_location", None) != contract["token_location"]
        or getattr(operation, "read_only", None) != contract["read_only"]
    ):
        return "operation_mismatch"
    regions = getattr(operation, "regions", frozenset())
    if contract["region"] not in regions:
        return "operation_mismatch"
    expected_kind = _expected_result_scenario(contract, scenario)
    if expected_kind == "response":
        if outcome_kind != "response":
            return "unexpected_exception"
        expected_message = (
            "***" if contract["operation_id"] in _REDACTED_MESSAGE_OPERATION_IDS else _MESSAGE_OK
        )
        problem = _check_response(
            outcome,
            package,
            success_payload,
            success_code,
            case["data"],
            expected_message,
        )
        if problem is not None:
            return problem
    else:
        if outcome_kind != "exception":
            return "expected_exception_absent"
        problem = _check_exception(outcome, package, scenario)
        if problem is not None:
            return problem
    expected_business, expected_auth = _expected_call_counts(contract, scenario)
    if len(queues.business_requests) != expected_business:
        return "request_count_mismatch"
    if len(queues.auth_requests) != expected_auth:
        return "auth_count_mismatch"
    return None


def _verify_auth_run(
    *,
    contract: dict,
    auth_contract: dict,
    api_origin: str,
    queues: _BoundedQueues,
    outcome_kind: str,
    outcome: object,
    scenario: str,
    package: object,
) -> str | None:
    if queues.exceeded:
        return "request_limit_exceeded"
    expected_origin = auth_contract["base_url"]
    for auth_request in queues.auth_requests:
        problem = _check_auth_request(auth_request, auth_contract, expected_origin)
        if problem is not None:
            return problem
    if scenario == _SUCCESS:
        if outcome_kind != "response":
            return "unexpected_exception"
        token = outcome
        if getattr(token, "value", None) != _auth_token_value(1):
            return "token_mismatch"
        if not _strict_equal(getattr(token, "expires_at", None), _TOKEN_EXPIRES_AT):
            return "token_mismatch"
        if getattr(token, "api_base_url", None) != api_origin:
            return "token_mismatch"
    else:
        if outcome_kind != "exception":
            return "expected_exception_absent"
        problem = _check_exception(outcome, package, scenario)
        if problem is not None:
            return problem
    if len(queues.auth_requests) != 1:
        return "auth_count_mismatch"
    if queues.business_requests:
        return "request_count_mismatch"
    return None


# ---------------------------------------------------------------------------
# Per-cell drivers
# ---------------------------------------------------------------------------


def _run_cell(
    *,
    package: object,
    contract: dict,
    case: dict | None,
    binding: dict | None,
    layout: str,
    scenario: str,
    mode: str,
    auth_contract: dict,
) -> str | None:
    """Execute one (mode, layout, scenario) cell; return a problem or None."""
    if contract["operation_id"] == _REFRESH_OPERATION:
        try:
            return asyncio.run(_run_refresh_cell(package, contract, case, layout, scenario, mode))
        except _Reject as exc:
            return exc.problem
        except Exception:
            return "harness_error"
    api_origin = _API_ORIGIN[contract["region"]]
    try:
        outcome_kind, outcome, queues, recorded = _run_coro(
            _execute_cell(
                package=package,
                contract=contract,
                case=case,
                binding=binding,
                layout=layout,
                scenario=scenario,
                mode=mode,
                auth_contract=auth_contract,
            )
        )
    except _Reject as exc:
        return exc.problem
    try:
        if contract["operation_id"] in _AUTH_OPERATION_IDS:
            return _verify_auth_run(
                contract=contract,
                auth_contract=auth_contract,
                api_origin=api_origin,
                queues=queues,
                outcome_kind=outcome_kind,
                outcome=outcome,
                scenario=scenario,
                package=package,
            )
        return _verify_operation_run(
            package=package,
            contract=contract,
            case=case,
            layout=layout,
            scenario=scenario,
            auth_contract=auth_contract,
            api_origin=api_origin,
            recorded=recorded,
            queues=queues,
            outcome_kind=outcome_kind,
            outcome=outcome,
        )
    except _Reject as exc:
        return exc.problem
    except Exception:
        return "harness_error"


async def _run_refresh_cell(package, contract, case, layout, scenario, mode) -> str | None:
    auth_origin = (
        "https://eval-auth-async.ezvizlife.com"
        if mode == "async"
        else "https://eval-auth-sync.ezvizlife.com"
    )
    api_origin = _API_ORIGIN["overseas"]
    data = case["data"]
    if not isinstance(data, dict):
        return "case_invalid"
    if scenario == "success":
        code, message, status = contract["success"]["values"][0], _MESSAGE_OK, 200
        tokens = (data.get("access_token"), data.get("refresh_token"))
        if any(not isinstance(token, str) or not token for token in tokens):
            return "case_invalid"
    elif scenario == "refresh_expired":
        code, message, status = "70004", _MESSAGE_FAULT, 200
    elif scenario == "binding_mismatch":
        code, message, status = "70005", _MESSAGE_FAULT, 200
    elif scenario == "http_fault":
        code, message, status = None, None, 500
    else:
        return "case_invalid"
    queue = _BoundedQueues(1, 0)

    def handler(request: httpx.Request) -> httpx.Response:
        queue.record_business(request)
        if status == 500:
            return httpx.Response(500)
        return httpx.Response(
            status, json=_build_payload(contract["success"], layout, code, message, data)
        )

    cls = package.OAuth if mode == "sync" else package.AsyncOAuth
    client = cls(_CONTEXT["app_key"], auth_origin=auth_origin, api_origin=api_origin)
    outcome = captured = None
    try:
        outcome = client.refresh_token(
            *case["args"], **case["kwargs"], transport=httpx.MockTransport(handler)
        )
        if mode == "async":
            outcome = await outcome
    except Exception as exc:
        captured = exc
    if queue.exceeded:
        return "request_limit_exceeded"
    if len(queue.business_requests) != 1:
        return "request_count_mismatch"
    actual = queue.business_requests[0]
    if str(actual.url) != auth_origin + contract["path"]:
        return "request_mismatch"
    expected = _expected_wire(contract, case["fields"], case["json_fields"], "")
    expected["origin"] = auth_origin
    expected["path"] = case["expected_path"]
    expected["ct_specs"] = case["ct_specs"]
    wire = _check_business_request(actual, contract, expected)
    if wire is not None:
        return wire
    if scenario == "success":
        if captured is not None:
            return "unexpected_exception"
        if type(outcome) is not package.OAuthRefreshResult:
            return "response_mismatch"
        pairs = (
            ("access_token", "access_token"),
            ("refresh_token", "refresh_token"),
            ("open_id", "openId"),
            ("expires_in", "expires_in"),
            ("scope", "scope"),
            ("state", "state"),
        )
        if any(not _strict_equal(getattr(outcome, attr), data.get(key)) for attr, key in pairs):
            return "response_mismatch"
        return None
    if captured is None:
        return "expected_exception_absent"
    if _MESSAGE_FAULT in str(captured) or _MESSAGE_FAULT in repr(captured):
        return "response_mismatch"
    exc_type = {
        "refresh_expired": package.AuthenticationError,
        "binding_mismatch": package.PermissionDeniedError,
    }.get(scenario, package.ApiError)
    if type(captured) is not exc_type:
        return "wrong_exception_type"
    if getattr(captured, "http_status", None) != status:
        return "wrong_exception_status"
    if scenario != _HTTP_FAULT and not _strict_equal(getattr(captured, "code", None), code):
        return "wrong_exception_status"
    return None


def _scenarios(operation_id: str) -> tuple[str, ...]:
    return _REFRESH_SCENARIOS if operation_id == _REFRESH_OPERATION else _SCENARIOS


def _emit_grid(emit, operation_id: str, layouts: list, problem: str) -> None:
    for mode in _MODES:
        for layout in layouts:
            for scenario in _scenarios(operation_id):
                emit(operation_id, mode, layout, scenario, problem)


def _run_case_all(emit, package, contract, binding, case, contract_map) -> None:
    operation_id = contract["operation_id"]
    is_auth_op = operation_id in _AUTH_OPERATION_IDS
    try:
        _check_contract(contract)
        if operation_id == _REFRESH_OPERATION:
            if (
                contract["region"] != "overseas"
                or contract["read_only"]
                or contract["token_location"] != "none"
                or contract.get("base_url") != "{authDomain}"
                or contract["success"]["values"] != ["200"]
            ):
                raise _Reject("contract_invalid")
            symbols = ["OAuth.refresh_token", "AsyncOAuth.refresh_token"]
            if (
                not isinstance(binding, dict)
                or binding.get("module") != "src/ezviz_unified/oauth.py"
                or binding.get("symbols") not in (symbols, symbols[::-1])
                or "public_entry" in binding
            ):
                raise _Reject("binding_invalid")
            auth_contract = contract
        elif is_auth_op:
            auth_contract = _check_auth_contract(contract)
        else:
            if binding is None:
                raise _Reject("binding_invalid")
            auth_contract = _check_auth_contract(
                contract_map.get(_AUTH_OPERATION_BY_REGION[contract["region"]])
            )
        layouts = _contract_layouts(contract)
    except _Reject as exc:
        _emit_grid(emit, operation_id, _contract_layouts(contract), exc.problem)
        return
    for mode in _MODES:
        for layout in layouts:
            for scenario in _scenarios(operation_id):
                problem = _run_cell(
                    package=package,
                    contract=contract,
                    case=case,
                    binding=binding,
                    layout=layout,
                    scenario=scenario,
                    mode=mode,
                    auth_contract=auth_contract,
                )
                emit(operation_id, mode, layout, scenario, problem)


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


def _row(operation_id: str, mode: str, layout: str, scenario: str, problem: str | None) -> dict:
    return {
        "operation_id": operation_id,
        "mode": mode,
        "layout": layout,
        "scenario": scenario,
        "passed": problem is None,
        "problem": problem,
    }


def _dto_registry(package: object) -> dict:
    registry = {}
    for name in _DTO_WHITELIST:
        cls = getattr(package, name, None)
        if cls is not None:
            registry[name] = cls
    return registry


def _emit_case_failure(emit, raw_case: object, contract_map: dict, problem: str) -> None:
    operation_id = raw_case.get("operation_id") if isinstance(raw_case, dict) else None
    contract = contract_map.get(operation_id) if isinstance(operation_id, str) else None
    if not isinstance(contract, dict):
        emit("", "", "", "", problem)
        return
    _emit_grid(emit, operation_id, _contract_layouts(contract), problem)


def evaluate(contracts: dict, bindings: dict, cases: dict, repo_root: Path) -> dict:
    """Evaluate SDK wire conformance offline and return a structured report."""
    contract_map = _contract_index(contracts)
    binding_map = _binding_index(bindings)
    results: list[dict] = []

    def emit(operation_id: str, mode: str, layout: str, scenario: str, problem: str | None) -> None:
        results.append(_row(operation_id, mode, layout, scenario, problem))

    cases_list = cases.get("cases") if isinstance(cases, dict) else None
    schema_ok = (
        isinstance(cases, dict)
        and type(cases.get("schema_version")) is int
        and cases["schema_version"] == _SCHEMA_VERSION
        and isinstance(cases_list, list)
    )
    package = None
    sdk_problem: str | None = None
    if schema_ok:
        package, sdk_problem = _import_sdk(repo_root)
    dto_registry = _dto_registry(package) if package is not None else {}

    if not schema_ok:
        emit("", "", "", "", "cases_invalid")
    else:
        seen: set[str] = set()
        for raw_case in cases_list:
            operation_id = raw_case.get("operation_id") if isinstance(raw_case, dict) else None
            try:
                if isinstance(operation_id, str):
                    if operation_id in seen:
                        raise _Reject("duplicate_operation_id")
                    seen.add(operation_id)
                if package is None:
                    raise _Reject(sdk_problem or "sdk_import_failed")
                case = _normalize_case(
                    raw_case,
                    contract_map,
                    _context_for_op(raw_case, contract_map),
                    dto_registry,
                )
            except _Reject as exc:
                _emit_case_failure(emit, raw_case, contract_map, exc.problem)
                continue
            contract = case["contract"]
            _run_case_all(
                emit,
                package,
                contract,
                binding_map.get(operation_id),
                case,
                contract_map,
            )

    passed_count = sum(1 for row in results if row["passed"])
    failed_count = len(results) - passed_count
    per_op: dict[str, bool] = {}
    for row in results:
        op = row["operation_id"]
        if not op:
            continue
        per_op[op] = per_op.get(op, True) and row["passed"]
    covered = sorted(op for op, ok in per_op.items() if ok)
    missing = sorted(set(contract_map) - set(covered))
    return {
        "schema_version": _SCHEMA_VERSION,
        "evidence": _EVIDENCE,
        "passed": failed_count == 0 and bool(results),
        "coverage_complete": not missing,
        "summary": {
            "contracts": len(contract_map),
            "cases": len(cases_list) if isinstance(cases_list, list) else 0,
            "executions": len(results),
            "passed_executions": passed_count,
            "failed_executions": failed_count,
        },
        "results": results,
        "covered_operation_ids": covered,
        "missing_operation_ids": missing,
    }
