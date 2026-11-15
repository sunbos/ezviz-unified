"""Explicit request contracts for the unified client.

``token_location`` selects where the client places the access token.
``"none"`` declares a public operation: the client never calls the token
provider, never injects a token into any field, never retries, and sends
the request to the configured ``api_base_url`` exactly as supplied.
"""

from dataclasses import dataclass, field
from typing import Literal

from ._path import parse_path_template
from .models import ResponsePolicy

TokenLocation = Literal["form", "query", "header", "json", "none"]


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    token_location: TokenLocation = field(default="form", kw_only=True)
    regions: frozenset[str] = field(default=frozenset({"domestic", "overseas"}), kw_only=True)
    read_only: bool = field(default=False, kw_only=True)
    policy: ResponsePolicy = field(default_factory=ResponsePolicy, kw_only=True)
    _path_params: tuple[str, ...] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("operation name must be a non-blank string")
        if self.method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            raise ValueError("operation method must be an uppercase HTTP method")
        if (
            not isinstance(self.path, str)
            or not self.path.startswith("/")
            or self.path.startswith("//")
            or any(ch in self.path for ch in ("?", "#", "\\"))
            or any(ord(ch) < 32 or ch.isspace() for ch in self.path)
        ):
            raise ValueError("operation path must be root-relative without query or fragment")
        if self.token_location not in {"form", "query", "header", "json", "none"}:
            raise ValueError("invalid token location")
        regions = frozenset(self.regions)
        if not regions or not regions <= {"domestic", "overseas"}:
            raise ValueError("operation regions must contain domestic and/or overseas")
        object.__setattr__(self, "regions", regions)
        if not isinstance(self.read_only, bool):
            raise ValueError("read_only must be a bool")
        if not isinstance(self.policy, ResponsePolicy):
            raise TypeError("operation policy must be a ResponsePolicy")
        # Whole-segment {name} placeholders are validated once here; the
        # template itself is kept verbatim and values are bound per request.
        object.__setattr__(self, "_path_params", parse_path_template(self.path))

    @property
    def path_params(self) -> tuple[str, ...] | None:
        """Template placeholder names, or ``None`` for a fixed path."""
        return self._path_params
