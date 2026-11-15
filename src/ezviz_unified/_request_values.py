"""Sentinel request values that differ from ``None`` omission semantics.

``JSON_NULL`` marks an explicit root-level JSON ``null`` body: the
transport serializes it as the four bytes ``null`` and defaults the
Content-Type to ``application/json``. Passing ``json=None`` keeps the
historical "no body" meaning — no bytes and no Content-Type are added.
Inside nested JSON values plain ``None`` already serializes to ``null``,
so the marker only makes sense at the body root.
"""

from __future__ import annotations

from typing import Final

__all__ = ["JSON_NULL"]


class _JsonNull:
    """Singleton marker type; identity is the only meaningful check."""

    __slots__ = ()

    _instance: _JsonNull | None = None

    def __new__(cls) -> _JsonNull:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "JSON_NULL"

    def __copy__(self) -> _JsonNull:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> _JsonNull:
        return self

    def __reduce__(self) -> str:
        return "JSON_NULL"


JSON_NULL: Final[_JsonNull] = _JsonNull()
