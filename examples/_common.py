"""Shared helpers for the device-list examples.

Responsible for environment configuration, the page-count summary and the
redacted error summary. Importing this module performs no I/O.
"""

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from ezviz_unified import EzvizError, RegionConfig

_REGIONS = ("domestic", "overseas")


class ConfigError(ValueError):
    """Raised for missing or invalid environment configuration."""

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(field)


@dataclass(frozen=True)
class ExampleConfig:
    """Validated credentials plus the SDK region configuration.

    Credentials are omitted from the default ``repr`` only; printing
    fields explicitly still exposes them.
    """

    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)
    region: str
    region_config: RegionConfig


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ConfigError(name)
    return value


def load_config() -> ExampleConfig:
    """Read and validate environment variables before any client exists."""
    app_key = _required("EZVIZ_APP_KEY")
    app_secret = _required("EZVIZ_APP_SECRET")
    region = _required("EZVIZ_REGION")
    if region not in _REGIONS:
        raise ConfigError("EZVIZ_REGION")
    override = os.environ.get("EZVIZ_AUTH_BASE_URL")
    if override is not None:
        # Once set, the variable must be usable: domestic rejects it
        # outright and empty/blank values are never silently ignored.
        if region == "domestic" or not override.strip():
            raise ConfigError("EZVIZ_AUTH_BASE_URL")
        try:
            region_config = RegionConfig.overseas(auth_base_url=override)
        except ValueError:
            # Report the field name only; never echo the rejected value.
            raise ConfigError("EZVIZ_AUTH_BASE_URL") from None
    elif region == "overseas":
        region_config = RegionConfig.overseas()
    else:
        region_config = RegionConfig.china()
    return ExampleConfig(app_key, app_secret, region, region_config)


def print_page_summary(config: ExampleConfig, items: list[Any]) -> None:
    """Print the success JSON: page metadata and the item count only."""
    print(
        json.dumps(
            {
                "status": "ok",
                "region": config.region,
                "page_start": 0,
                "page_size": 1,
                "returned_items": len(items),
            }
        )
    )


def print_failure(exc: BaseException) -> None:
    """Print a redacted failure JSON; never echoes payloads or secrets."""
    if isinstance(exc, ConfigError):
        report: dict[str, Any] = {
            "status": "failed",
            "error_type": "config_error",
            "field": exc.field,
        }
    elif isinstance(exc, EzvizError):
        # Trusted SDK exceptions only: surface the real class name so callers
        # can tell auth, permission, protocol and transport failures apart.
        report = {"status": "failed", "error_type": type(exc).__name__}
        code = getattr(exc, "code", None)
        if isinstance(code, int) and not isinstance(code, bool):
            report["code"] = code
        elif isinstance(code, str) and code.isascii() and code.isdigit():
            report["code"] = code
        http_status = getattr(exc, "http_status", None)
        if isinstance(http_status, int) and not isinstance(http_status, bool):
            report["http_status"] = http_status
    else:
        report = {"status": "failed", "error_type": "unexpected_response"}
    print(json.dumps(report), file=sys.stderr)
