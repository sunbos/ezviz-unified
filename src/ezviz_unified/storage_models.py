"""Unified storage parameter and query models.

Pure validation helpers for ``storage.py``: they check caller input and build
plain ``dict`` payloads. No HTTP client, no I/O, and no server-side defaults
are injected here — ``None`` always means "omit the parameter".
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

__all__ = ["StorageFileQuery"]

_BIZ_TYPES = ("vod", "record", "cloudCapture", "capture")
_SPACE_SORT_RULES = ("asc", "desc")
_SPACE_SORT_FIELDS = ("createTime", "updateTime", "totalSize")
_SPACE_QUERY_MAX_WINDOW = timedelta(days=30)
_DOWNLOAD_MAX_IDS = 50


def _text(name: str, value: object) -> str:
    """Return ``value`` unchanged when it is a string with non-whitespace content."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _integer(name: str, value: object) -> int:
    """Return ``value`` when it is an ``int``; ``bool`` is rejected."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _name_char(char: str) -> bool:
    if "a" <= char <= "z" or "A" <= char <= "Z" or "0" <= char <= "9" or char == "_":
        return True
    try:
        name = unicodedata.name(char)
    except ValueError:
        return False
    return name.startswith(("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-"))


def _name(value: object) -> str:
    """Validate a space name: 1..32 chars of ASCII letters/digits/underscore/CJK."""
    if not isinstance(value, str) or not 1 <= len(value) <= 32:
        raise ValueError("space name must be a string of 1..32 characters")
    if not all(_name_char(char) for char in value):
        raise ValueError(
            "space name may only contain letters, digits, underscores and CJK ideographs"
        )
    return value


def _biz_type(value: object) -> str:
    if not isinstance(value, str) or value not in _BIZ_TYPES:
        raise ValueError("biz type must be one of: vod, record, cloudCapture, capture")
    return value


def _retention(value: object) -> int:
    """Validate ``expireDays``: ``-1`` (permanent) or ``1..5475`` days."""
    days = _integer("expire days", value)
    if days != -1 and not 1 <= days <= 5475:
        raise ValueError("expire days must be -1 or within 1..5475")
    return days


def _sequence_items(name: str, values: object) -> tuple[object, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{name} must be a non-string sequence")
    return tuple(values)


def _node_id_tuple(values: object) -> tuple[str, ...]:
    items = _sequence_items("node ids", values)
    if not items:
        raise ValueError("node ids must not be empty")
    nodes: list[str] = []
    for item in items:
        node = _text("node id", item)
        if "," in node:
            raise ValueError("node id must not contain commas")
        nodes.append(node)
    return tuple(nodes)


def _node_ids(values: Sequence[str], *, limit: int | None = None) -> str:
    """Validate file-node ids and join them with commas.

    ``limit`` enforces a documented per-call maximum; ``None`` applies no
    undeclared cap. Order and duplicates are preserved.
    """
    nodes = _node_id_tuple(values)
    if limit is not None and len(nodes) > _integer("limit", limit):
        raise ValueError("node ids exceed the documented limit")
    return ",".join(nodes)


def _space_id_tuple(values: object) -> tuple[int, ...]:
    items = _sequence_items("space ids", values)
    if not 1 <= len(items) <= 5:
        raise ValueError("space ids must contain 1..5 entries")
    return tuple(_integer("space id", item) for item in items)


def _space_fields(
    *,
    name: str | None = None,
    expire_days: int | None = None,
    storage_type: int | None = None,
    flow_limit: int | None = None,
    permission: int | None = None,
) -> dict[str, str | int]:
    """Build the updatable space field map; ``None`` values are omitted."""
    fields: dict[str, str | int] = {}
    if name is not None:
        fields["spaceName"] = _name(name)
    if expire_days is not None:
        fields["expireDays"] = _retention(expire_days)
    if storage_type is not None:
        storage = _integer("storage_type", storage_type)
        if storage not in (1, 2):
            raise ValueError("storage_type must be 1 or 2")
        fields["storageType"] = storage
    if flow_limit is not None:
        fields["flowLimit"] = _integer("flow_limit", flow_limit)
    if permission is not None:
        perm = _integer("permission", permission)
        if perm not in (1, 2):
            raise ValueError("permission must be 1 or 2")
        fields["permission"] = perm
    return fields


def _moment(name: str, value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is not None or value.microsecond != 0:
        raise ValueError(f"{name} must be a naive datetime with whole seconds")
    return value


def _format_time(value: datetime) -> str:
    """Format as ``yyyy-MM-dd HH:mm:ss`` with a four-digit year."""
    return (
        f"{value.year:04d}-{value.month:02d}-{value.day:02d} "
        f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}"
    )


def _spaces_query(
    *,
    start_time: datetime,
    end_time: datetime,
    last_space_id: int | None = None,
    page_size: int | None = None,
    biz_type: str | None = None,
    sort_rule: str | None = None,
    sort_field: str | None = None,
) -> dict[str, str | int]:
    """Build query parameters for the space list endpoint.

    ``start_time``/``end_time`` must be naive whole-second datetimes with
    ``start <= end`` and a span of at most 30 days. Optional parameters are
    omitted when ``None``.
    """
    start = _moment("start_time", start_time)
    end = _moment("end_time", end_time)
    if start > end:
        raise ValueError("start_time must not be later than end_time")
    if end - start > _SPACE_QUERY_MAX_WINDOW:
        raise ValueError("start_time/end_time span must not exceed 30 days")
    query: dict[str, str | int] = {
        "startTime": _format_time(start),
        "endTime": _format_time(end),
    }
    if last_space_id is not None:
        query["lastSpaceId"] = _integer("last_space_id", last_space_id)
    if page_size is not None:
        size = _integer("page_size", page_size)
        if not 1 <= size <= 50:
            raise ValueError("page_size must be within 1..50")
        query["pageSize"] = size
    if biz_type is not None:
        query["bizTypeList"] = _biz_type(biz_type)
    if sort_rule is not None:
        if sort_rule not in _SPACE_SORT_RULES:
            raise ValueError("sort_rule must be 'asc' or 'desc'")
        query["sortRule"] = sort_rule
    if sort_field is not None:
        if sort_field not in _SPACE_SORT_FIELDS:
            raise ValueError("sort_field must be createTime, updateTime or totalSize")
        query["sortField"] = sort_field
    return query


def _download_query(
    file_node_ids: Sequence[str], *, expire_seconds: int | None = None
) -> dict[str, str | int]:
    """Build query parameters for the download-urls endpoint.

    ``fileNodeIds`` is required (1..50 entries); ``expireSeconds`` is omitted
    when ``None`` and must otherwise be within 30..604800.
    """
    query: dict[str, str | int] = {"fileNodeIds": _node_ids(file_node_ids, limit=_DOWNLOAD_MAX_IDS)}
    if expire_seconds is not None:
        seconds = _integer("expire_seconds", expire_seconds)
        if not 30 <= seconds <= 604800:
            raise ValueError("expire_seconds must be within 30..604800")
        query["expireSeconds"] = seconds
    return query


@dataclass(frozen=True, kw_only=True)
class StorageFileQuery:
    """Validated parameters for ``file/listById`` (search files in a space).

    At least one selector is required. The documented priority
    ``fileNodeIds > tagId > spaceIds`` is resolved by the service: every
    selector supplied here is sent verbatim, none are merged or dropped.
    When it is the active selector, ``space_ids`` requires both time bounds; other modes
    may pass either time independently, and times are sent even though the
    source marks them ineffective for ``fileNodeIds``.
    """

    file_node_ids: Sequence[str] | None = field(default=None, repr=False)
    space_ids: Sequence[int] | None = field(default=None, repr=False)
    tag_id: str | None = field(default=None, repr=False)
    start_time: datetime | None = None
    end_time: datetime | None = None
    file_type: int | None = None
    file_child_type: str | None = None
    page_size: int | None = None
    last_id: str | None = field(default=None, repr=False)
    sort_rule: int | None = None
    source_type: int | None = None

    def __post_init__(self) -> None:
        if self.file_node_ids is not None:
            object.__setattr__(self, "file_node_ids", _node_id_tuple(self.file_node_ids))
        if self.space_ids is not None:
            object.__setattr__(self, "space_ids", _space_id_tuple(self.space_ids))
        if self.tag_id is not None:
            _text("tag_id", self.tag_id)
        if self.start_time is not None:
            _moment("start_time", self.start_time)
        if self.end_time is not None:
            _moment("end_time", self.end_time)
        if self.file_type is not None:
            file_type = _integer("file_type", self.file_type)
            if not 0 <= file_type <= 4:
                raise ValueError("file_type must be within 0..4")
        if self.file_child_type is not None:
            _text("file_child_type", self.file_child_type)
        if self.page_size is not None:
            page_size = _integer("page_size", self.page_size)
            if not 1 <= page_size <= 50:
                raise ValueError("page_size must be within 1..50")
        if self.last_id is not None and (
            not isinstance(self.last_id, str) or (self.last_id != "" and not self.last_id.strip())
        ):
            raise ValueError("last_id must be an empty or non-blank string")
        if self.sort_rule is not None:
            sort_rule = _integer("sort_rule", self.sort_rule)
            if sort_rule not in (0, 1):
                raise ValueError("sort_rule must be 0 or 1")
        if self.source_type is not None:
            _integer("source_type", self.source_type)
        if self.file_node_ids is None and self.tag_id is None and self.space_ids is None:
            raise ValueError("at least one selector is required")
        if (
            self.file_node_ids is None
            and self.tag_id is None
            and self.space_ids is not None
            and (self.start_time is None or self.end_time is None)
        ):
            raise ValueError("space_ids requires both start_time and end_time")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time > self.end_time
        ):
            raise ValueError("start_time must not be later than end_time")

    def to_form(self) -> dict[str, str | int]:
        """Return a freshly built form payload; ``None`` fields are omitted."""
        form: dict[str, str | int] = {}
        if self.file_node_ids is not None:
            form["fileNodeIds"] = ",".join(self.file_node_ids)
        if self.space_ids is not None:
            # No multi-space example in the source; CSV mirrors fileNodeIds.
            form["spaceIds"] = ",".join(str(space_id) for space_id in self.space_ids)
        if self.tag_id is not None:
            form["tagId"] = self.tag_id
        if self.start_time is not None:
            form["startTime"] = _format_time(self.start_time)
        if self.end_time is not None:
            form["endTime"] = _format_time(self.end_time)
        if self.file_type is not None:
            form["fileType"] = str(self.file_type)  # declared String on the wire
        if self.file_child_type is not None:
            form["fileChildType"] = self.file_child_type
        if self.page_size is not None:
            form["pageSize"] = self.page_size
        if self.last_id is not None:
            form["lastId"] = self.last_id
        if self.sort_rule is not None:
            form["sortRule"] = self.sort_rule
        if self.source_type is not None:
            form["sourceType"] = self.source_type
        return form
