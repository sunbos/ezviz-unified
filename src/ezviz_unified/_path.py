"""Path-template parsing and single-segment rendering for operations.

An operation path may contain whole-segment ``{name}`` placeholders.
Names are ASCII identifiers; braces must be balanced, un-nested, and
carry no format spec or conversion. Static segments of a templated path
must not be ``.``/``..`` — including percent-encoded spellings such as
``%2e%2e`` — because URL parsers normalize dot segments away.

``render_path`` validates the caller's values (non-string, blank,
control characters, lone ``.``/``..``, raw ``/`` or ``\\``, and values
that cannot be UTF-8 encoded are all refused with a fixed ``ValueError``)
and returns the rendered path plus the raw and encoded parameter values
for the request's redaction context. Parameters are single unencoded
segments: they are percent-encoded exactly once, so a literal ``%`` in
the value is data (``a%2Fb`` becomes ``a%252Fb``). Validation messages
and exception chains never embed raw or encoded parameter values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import quote

_PLACEHOLDER_RE = re.compile(r"\A\{([A-Za-z_][A-Za-z0-9_]*)\}\Z")
_HEX_DIGITS_RE = re.compile(r"%[0-9A-Fa-f]{2}")

_ERROR = "path_params must supply one non-blank string segment per template placeholder"

# C0/C1 controls plus DEL are rejected in parameter values.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def _decode_ascii_entities(segment: str) -> str:
    """Decode only ASCII-producing ``%XX`` entities; leave the rest verbatim.

    Triplets at or above 0x80 are kept untouched so the check answers
    "does the decoded segment equal a dot segment" without ever treating
    an encoded non-ASCII byte sequence as navigation.
    """

    def replace(match: re.Match[str]) -> str:
        code = int(match.group(0)[1:], 16)
        return chr(code) if code < 0x80 else match.group(0)

    return _HEX_DIGITS_RE.sub(replace, segment)


def _is_dot_segment(segment: str) -> bool:
    """True when *segment* resolves to ``.`` or ``..`` after decoding."""
    return _decode_ascii_entities(segment) in (".", "..")


def parse_path_template(path: str) -> tuple[str, ...] | None:
    """Validate template syntax; return placeholder names or ``None``.

    ``None`` marks a fixed path. A tuple (possibly with duplicate names,
    since a placeholder may repeat) marks a template. ``ValueError`` is
    raised for every malformed construct, always with a fixed message.
    Every ``{`` and ``}`` in the path must be part of exactly one
    whole-segment ``{name}`` placeholder; embedded braces, reversed
    braces, format specs, and conversions are all rejected.
    """
    names: list[str] = []
    templated = False
    for segment in path.split("/"):
        match = _PLACEHOLDER_RE.fullmatch(segment)
        if match is not None:
            names.append(match.group(1))
            templated = True
            continue
        # Any remaining brace in a segment is malformed: partial braces,
        # nested braces, reversed braces (``}x{``), format/conversion
        # syntax, and embedded placeholders are all rejected here.
        if "{" in segment or "}" in segment:
            raise ValueError(
                "operation path placeholders must be balanced whole segments like {name}"
            )
    if not templated:
        return None
    # Static segments of a templated path must not be dot segments, even
    # percent-encoded: URL parsers would normalize them into navigation.
    for segment in path.split("/"):
        if "{" not in segment and _is_dot_segment(segment):
            raise ValueError("templated path must not contain . or .. segments")
    return tuple(names)


def _check_value(value: str) -> None:
    """Single-segment rules; raises the fixed ``ValueError`` on any failure."""
    if not value.strip():
        raise ValueError(_ERROR)
    if _CONTROL_RE.search(value) or "/" in value or "\\" in value:
        raise ValueError(_ERROR)
    if value in (".", ".."):
        raise ValueError(_ERROR)
    try:
        value.encode("utf-8")  # lone surrogates fail as UnicodeEncodeError
    except UnicodeEncodeError:
        encodable = False
    else:
        encodable = True
    # Raised outside the except block so no UnicodeEncodeError (which embeds
    # the offending value) is left on the new exception's __context__.
    if not encodable:
        raise ValueError(_ERROR)


def render_path(
    template: str,
    names: tuple[str, ...],
    path_params: Mapping[str, str] | None,
) -> tuple[str, tuple[str, ...]]:
    """Bind caller values to placeholders; encode each segment once.

    Returns ``(rendered_path, secrets)`` where ``secrets`` lists every raw
    and encoded parameter value for the transport's redaction context.
    Any rejected input raises a fixed ``ValueError`` that never carries
    the offending value or its encoding.
    """
    if not isinstance(path_params, Mapping):
        raise ValueError(_ERROR)
    supplied: dict[str, str] = {}
    for key, value in path_params.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(_ERROR)
        supplied[key] = value
    required = set(names)
    if set(supplied) != required:
        raise ValueError(_ERROR)
    for value in supplied.values():
        _check_value(value)
    secrets: list[str] = []

    def encode(name: str) -> str:
        raw = supplied[name]
        encoded = quote(raw, safe="")
        secrets.append(raw)
        secrets.append(encoded)
        return encoded

    rendered = "/".join(
        encode(match.group(1)) if (match := _PLACEHOLDER_RE.fullmatch(segment)) else segment
        for segment in template.split("/")
    )
    return rendered, tuple(secrets)
