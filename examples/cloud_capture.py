"""Explicit cloud-capture helpers plus a single-query task CLI.

Each helper performs exactly one SDK call and returns the ``ApiResponse``
unchanged. The caller owns the client, selectors, page inspection and
error handling; nothing here follows ``hasNext``, derives file node ids
from a task response, or fetches returned media URLs. Importing this
module performs no I/O.

Task creation is a real write, documented here only; the CLI never
executes it. Real signatures (keyword-only after ``device_serial``;
``rec_type`` is ``"local"``, ``"cloud"`` or ``"live"``)::

    create_interval_task(device_serial, *, channel, rec_type,
        start_time, end_time, frame_interval, project_id=None,
        result_space_id=None, frame_model=None, dev_proto=None,
        stream_type=None, validate_code=None)
    create_timing_task(device_serial, *, channel, rec_type,
        timing_points, project_id=None, result_space_id=None)

The two creation blocks below are independent options — choose at most
one; a single copied flow must not create both task kinds.

Option A — interval capture::

    from datetime import datetime

    created = client.cloud_capture.create_interval_task(
        "C00000000",
        channel=1,
        rec_type="cloud",
        start_time=datetime(2026, 1, 5, 8, 0, 0),
        end_time=datetime(2026, 1, 5, 9, 0, 0),
        frame_interval=60,
        result_space_id=900001,  # synthetic pre-existing space
    )
    task_id = created.data["taskId"]  # caller verifies it is a str

Option B — capture at explicit time points (alternative, not additive)::

    from datetime import datetime

    created = client.cloud_capture.create_timing_task(
        "C00000000",
        channel=1,
        rec_type="local",
        timing_points=[datetime(2026, 1, 5, 8, 30, 0)],
        project_id="SYNTHETIC_PROJECT",
    )
    task_id = created.data["taskId"]  # caller verifies it is a str

With ``task_id`` kept locally — created above or issued earlier — the
read-only chain stays fully explicit (sync names shown)::

    task = query_task_sync(client, task_id)
    page = search_files_sync(
        client,
        StorageFileQuery(tag_id=task_id, page_size=1, last_id=""),
    )
    file_node_ids = ["example-file-node-id"]  # picked after inspecting
    urls = download_urls_sync(client, file_node_ids)

Async variants await one call each; creation takes the same kwargs::

    created = await client.cloud_capture.create_interval_task(
        "C00000000",
        channel=1,
        rec_type="cloud",
        start_time=datetime(2026, 1, 5, 8, 0, 0),
        end_time=datetime(2026, 1, 5, 9, 0, 0),
        frame_interval=60,
        result_space_id=900001,
    )
    task = await query_task_async(client, task_id)
    page = await search_files_async(client, query)
    urls = await download_urls_async(client, file_node_ids)

Selector priority (``file_node_ids`` > ``tag_id`` > ``space_ids``) is
resolved server-side; ``tag_id`` + ``space_ids`` is not an AND filter.
``taskStatus`` ``0`` (completed) and ``3`` (ended) stay distinct, and a
successful query never proves produced media exists or is playable.

CLI: ``python -m examples.cloud_capture --mode sync|async`` queries the
task id stored in ``EZVIZ_CLOUD_TASK_ID`` once and prints a whitelisted
JSON summary.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from typing import NoReturn

from examples._common import ConfigError, ExampleConfig, load_config
from ezviz_unified import (
    ApiResponse,
    AsyncClient,
    Client,
    EzvizError,
    StorageFileQuery,
)

_TASK_ID_ENV = "EZVIZ_CLOUD_TASK_ID"

_TASK_STATES = {
    0: "completed",
    1: "queued",
    2: "running",
    3: "ended",
    4: "abnormal_end",
    5: "paused",
    6: "cancelled",
    7: "not_started",
}


def query_task_sync(client: Client, task_id: str) -> ApiResponse:
    """Query one cloud-capture task with a single SDK call."""
    return client.cloud_capture.task(task_id)


async def query_task_async(client: AsyncClient, task_id: str) -> ApiResponse:
    """Async variant of :func:`query_task_sync`."""
    return await client.cloud_capture.task(task_id)


def search_files_sync(client: Client, query: StorageFileQuery) -> ApiResponse:
    """Request exactly one storage file page with a single SDK call."""
    return client.storage.search_files(query)


async def search_files_async(client: AsyncClient, query: StorageFileQuery) -> ApiResponse:
    """Async variant of :func:`search_files_sync`."""
    return await client.storage.search_files(query)


def download_urls_sync(
    client: Client,
    file_node_ids: Sequence[str],
    *,
    expire_seconds: int | None = None,
) -> ApiResponse:
    """Fetch download addresses once; the media itself is not fetched."""
    return client.storage.download_urls(file_node_ids, expire_seconds=expire_seconds)


async def download_urls_async(
    client: AsyncClient,
    file_node_ids: Sequence[str],
    *,
    expire_seconds: int | None = None,
) -> ApiResponse:
    """Async variant of :func:`download_urls_sync`."""
    return await client.storage.download_urls(file_node_ids, expire_seconds=expire_seconds)


class _ArgumentError(Exception):
    """Private signal: parsing failed; no argv or details were echoed."""


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never echoes argv or per-argument details."""

    def error(self, message: str) -> NoReturn:
        del message  # fixed signal only; the default error would echo argv
        raise _ArgumentError


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m examples.cloud_capture",
        description="Query one cloud-capture task exactly once.",
    )
    parser.add_argument("--mode", choices=("sync", "async"), required=True)
    return parser


def _task_state(response: ApiResponse) -> str | None:
    """Map ``taskStatus`` to the whitelist; ``None`` for non-dict data."""
    data = response.data
    if not isinstance(data, dict):
        return None
    status = data.get("taskStatus")
    if type(status) is not int:  # bool/float/str/other all become unknown
        return "unknown"
    return _TASK_STATES.get(status, "unknown")


def _emit_ok(mode: str, task_state: str) -> None:
    print(
        json.dumps(
            {
                "status": "ok",
                "mode": mode,
                "logical_calls": 1,
                "task_state": task_state,
            }
        )
    )


def _fail(category: str) -> int:
    print(
        json.dumps({"status": "failed", "category": category}),
        file=sys.stderr,
    )
    return 1


def _query_once_sync(config: ExampleConfig, task_id: str) -> ApiResponse:
    with Client(config.app_key, config.app_secret, region=config.region_config) as client:
        return query_task_sync(client, task_id)


async def _query_once_async(config: ExampleConfig, task_id: str) -> ApiResponse:
    async with AsyncClient(
        config.app_key, config.app_secret, region=config.region_config
    ) as client:
        return await query_task_async(client, task_id)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments first, then run exactly one task query."""
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentError:
        return _fail("input_error")
    try:
        config = load_config()
        task_id = os.environ.get(_TASK_ID_ENV)
        if task_id is None or not task_id.strip():
            raise ConfigError(_TASK_ID_ENV)
        if config.region != "domestic":
            raise ConfigError("EZVIZ_REGION")
        if args.mode == "sync":
            response = _query_once_sync(config, task_id)
        else:
            response = asyncio.run(_query_once_async(config, task_id))
    except ConfigError:
        return _fail("configuration_error")
    except EzvizError:
        return _fail("sdk_error")
    except (ValueError, TypeError):
        return _fail("input_error")
    except Exception:
        return _fail("unexpected_response")
    task_state = _task_state(response)
    if task_state is None:
        return _fail("unexpected_response")
    _emit_ok(args.mode, task_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
