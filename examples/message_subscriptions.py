"""Bounded message-subscription consumer example plus a one-shot observe CLI.

The synchronous/asynchronous ``run_*`` helpers create a single consumer on a
caller-owned (borrowed) client, read a fixed number of batches with no
offsets, retries, sleeps, threads or background tasks, and hand every raw
consume :class:`ApiResponse` to a mandatory handler before the next read.

CLI boundary: ``python -m examples.message_subscriptions --mode {sync,async}
--execute`` performs a private one-shot observation path — it does *not* call
``run_sync``/``run_async`` — that creates exactly one consumer, validates the
opaque id, consumes exactly one batch, and confirms ``data`` is a list. It
performs no message processing, no extra final read and no separate final
commit — though the single ``consume`` itself may commit the previous batch
on the server, and ``final_commit_confirmed`` stays ``False`` because there
is no dedicated confirmation step. ``--execute`` only confirms the start;
the create/consume calls still change server-side state and are not a
read-only probe. Argument/help failures and ``import`` read no environment
and create no client.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, NoReturn, get_args

from examples._common import ConfigError, ExampleConfig, load_config
from ezviz_unified import (
    ApiError,
    ApiResponse,
    AsyncClient,
    AuthenticationError,
    Client,
    EzvizError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

__all__ = ["ConsumerGroup", "MessageRunResult", "main", "run_async", "run_sync"]

ConsumerGroup = Literal["group1", "group2", "group3", "group4", "group5"]

# Membership check mirrors the Literal; a group is never auto-selected.
_ALLOWED_GROUPS = frozenset(get_args(ConsumerGroup))
_MIN_BATCHES = 1
_MAX_BATCHES = 10


@dataclass(frozen=True, slots=True)
class MessageRunResult:
    """Local summary returned only when a bounded run finishes normally.

    ``consumer_id`` is hidden from ``repr`` as a readability choice, not
    access control: callers can still read the field to continue consuming
    with the same id themselves. ``final_commit_confirmed`` is always
    ``False`` because this example performs no separate audited commit of
    the final batch. The result carries no raw payloads, offsets, response
    objects or lifecycle methods, and must not be asdict/json-dumped whole.
    """

    consumer_id: str = field(repr=False)
    batches_received: int
    handlers_completed: int
    messages_observed: int
    final_commit_confirmed: Literal[False] = False


def _is_generator_handler(handler: object) -> bool:
    """Inspect only: detect generator/async-generator callables.

    The handler's contract is ``Callable[..., None]``/``Awaitable[None]``; a
    (async) generator function returns an iterator whose body would not run
    under either run's return-value checks, so both modes reject it here.
    Neither the handler nor ``__call__`` is invoked or advanced.
    """
    return (
        inspect.isgeneratorfunction(handler)
        or inspect.isasyncgenfunction(handler)
        or inspect.isgeneratorfunction(getattr(handler, "__call__", None))
        or inspect.isasyncgenfunction(getattr(handler, "__call__", None))
    )


def _validate_request(group: object, handler: object, max_batches: object) -> None:
    """Fail fast on locally detectable contract violations, before any create."""
    if isinstance(max_batches, bool) or not isinstance(max_batches, int):
        raise TypeError("max_batches must be a real int, not a bool")
    if not _MIN_BATCHES <= max_batches <= _MAX_BATCHES:
        raise ValueError("max_batches must be between 1 and 10 inclusive")
    if not callable(handler):
        raise TypeError("handler must be callable")
    if _is_generator_handler(handler):
        raise TypeError("handler must not be a generator or async generator function")
    if not isinstance(group, str) or group not in _ALLOWED_GROUPS:
        raise ValueError("group must be one of the five fixed consumer groups")


def _created_consumer_id(response: ApiResponse) -> str:
    """Extract the opaque consumer id; the value is checked, never modified."""
    data = response.data
    if not isinstance(data, dict):
        raise ValueError("create_consumer response data must be a dict")
    consumer_id = data.get("consumerId")
    if not isinstance(consumer_id, str) or not consumer_id.strip():
        raise ValueError("create_consumer response needs a non-blank consumerId")
    try:
        consumer_id.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("consumerId must be a valid UTF-8 str") from None
    return consumer_id


def _batch_message_count(batch: ApiResponse) -> int:
    """Return ``len(batch.data)`` after confirming the payload is a list."""
    data = batch.data
    if not isinstance(data, list):
        raise ValueError("consume response data must be a list")
    return len(data)


def run_sync(
    client: Client,
    group: ConsumerGroup,
    *,
    handler: Callable[[ApiResponse], None],
    max_batches: int,
) -> MessageRunResult:
    """Create one consumer on ``client`` and consume ``max_batches`` batches.

    The client is borrowed: it is not closed and its context manager is never
    entered. ``handler`` must be a plain synchronous callable; it receives
    each raw consume ``ApiResponse`` and must finish before the next read.
    Local contract violations raise ``TypeError``/``ValueError`` with fixed,
    input-free messages; SDK and handler exceptions propagate unchanged and
    no partial result is returned.
    """
    _validate_request(group, handler, max_batches)
    if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
        getattr(handler, "__call__", None)
    ):
        raise TypeError("run_sync requires a synchronous handler; use run_async")
    # Generator/async-generator callables were already rejected by
    # _validate_request; the coroutinefunction check above stays sync-only.

    response = client.message_subscriptions.create_consumer(group)
    consumer_id = _created_consumer_id(response)

    batches_received = 0
    handlers_completed = 0
    messages_observed = 0
    for _ in range(max_batches):
        batch = client.message_subscriptions.consume(consumer_id)
        count = _batch_message_count(batch)
        batches_received += 1
        messages_observed += count
        produced = handler(batch)
        if inspect.isawaitable(produced):
            if inspect.iscoroutine(produced):
                produced.close()  # avoid "never awaited" warning; do not await
            raise TypeError("run_sync handler returned an awaitable")
        if produced is not None:
            # The contract is handler(...) -> None; a stray non-None return
            # (including a generator/async-generator iterator that slipped
            # past creation-time checks) is a contract violation. We never
            # next()/send()/await it — the run stops with a fixed TypeError
            # and the handler does not count as completed.
            raise TypeError("run_sync handler must return None")
        handlers_completed += 1

    return MessageRunResult(
        consumer_id=consumer_id,
        batches_received=batches_received,
        handlers_completed=handlers_completed,
        messages_observed=messages_observed,
        final_commit_confirmed=False,
    )


async def run_async(
    client: AsyncClient,
    group: ConsumerGroup,
    *,
    handler: Callable[[ApiResponse], Awaitable[None]],
    max_batches: int,
) -> MessageRunResult:
    """Async twin of :func:`run_sync` on a borrowed :class:`AsyncClient`.

    ``handler`` may be any callable provided each call returns an awaitable,
    which is awaited before the next consume; a non-awaitable return stops
    the run. Cancellation and SDK/handler exceptions propagate unchanged.
    """
    _validate_request(group, handler, max_batches)

    response = await client.message_subscriptions.create_consumer(group)
    consumer_id = _created_consumer_id(response)

    batches_received = 0
    handlers_completed = 0
    messages_observed = 0
    for _ in range(max_batches):
        batch = await client.message_subscriptions.consume(consumer_id)
        count = _batch_message_count(batch)
        batches_received += 1
        messages_observed += count
        produced = handler(batch)
        if not inspect.isawaitable(produced):
            raise TypeError("run_async handler must return an awaitable")
        awaited_result = await produced
        if awaited_result is not None:
            # The contract is handler(...) -> Awaitable[None]; a non-None
            # await result is a contract violation. It does not count as a
            # completed handler and the next batch is never consumed.
            raise TypeError("run_async handler awaitable must resolve to None")
        handlers_completed += 1

    return MessageRunResult(
        consumer_id=consumer_id,
        batches_received=batches_received,
        handlers_completed=handlers_completed,
        messages_observed=messages_observed,
        final_commit_confirmed=False,
    )


# ---------------------------------------------------------------------------
# Command-line one-shot observation path.
#
# This CLI never calls run_sync/run_async: it uses its own private path that
# creates exactly one consumer, validates the opaque id, consumes exactly one
# batch, and confirms ``data`` is a list. It performs no message processing,
# no extra final read and no separate final commit — though the single
# ``consume`` itself may commit the previous batch on the server, and
# ``final_commit_confirmed`` stays ``False`` because there is no dedicated
# confirmation step. ``--execute`` only confirms the start; the
# create/consume calls still change server-side state and are not a
# read-only probe.
# ---------------------------------------------------------------------------

_GROUP_ENV = "EZVIZ_MESSAGE_GROUP"


class _ArgumentError(Exception):
    """Private signal: parsing failed; no argv or details are echoed."""


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that never echoes argv or per-argument details."""

    def error(self, message: str) -> NoReturn:
        del message  # fixed signal only; the default error would echo argv
        raise _ArgumentError


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m examples.message_subscriptions",
        description=(
            "Create one message consumer and observe a single batch. "
            "No message processing, no extra final read and no separate "
            "final commit; the consume itself may commit the previous batch."
        ),
    )
    parser.add_argument("--mode", choices=("sync", "async"), required=True)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="confirm the single create+consume observation run",
    )
    return parser


def _emit(report: dict[str, Any]) -> None:
    print(json.dumps(report))


def _failure_report(mode: str | None, stage: str, family: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": 1,
        "outcome": "failed",
        "stage": stage,
        "error_family": family,
        "processing_performed": False,
        "final_commit_confirmed": False,
    }
    if mode is not None:
        report["mode"] = mode
    return report


def _fail(mode: str | None, stage: str, family: str, code: int) -> int:
    _emit(_failure_report(mode, stage, family))
    return code


def _success_report(mode: str, messages_observed: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": mode,
        "outcome": "observed",
        "stage": "complete",
        "error_family": "none",
        "batches_received": 1,
        "messages_observed": messages_observed,
        "processing_performed": False,
        "final_commit_confirmed": False,
    }


def _error_family(exc: BaseException, stage: str) -> str:
    if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
        return "interrupted"
    if isinstance(exc, ConfigError):
        return "configuration"
    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, PermissionDeniedError):
        return "permission"
    if isinstance(exc, ProtocolError):
        return "protocol"
    if isinstance(exc, TransportError):
        return "transport"
    if isinstance(exc, (ApiError, EzvizError)):
        return "api"
    if isinstance(exc, (ValueError, TypeError)):
        # Local shape/contract violations observed during create or consume
        # are response-shape problems; at the configuration stage they are
        # configuration problems. Only those stages reach this mapper.
        return "protocol" if stage in ("create", "consume") else "configuration"
    return "unknown"


def _load_configuration() -> tuple[ExampleConfig, str]:
    """Load config and validate the group env exactly once.

    Returns ``(config, validated_group)`` so ``main`` never re-reads
    ``os.environ`` — a second read would race with concurrent env mutation.
    """
    config = load_config()
    group = os.environ.get(_GROUP_ENV)
    if group is None or group not in _ALLOWED_GROUPS:
        raise ConfigError(_GROUP_ENV)
    if config.region != "domestic":
        raise ConfigError("EZVIZ_REGION")
    return config, group


def _observe_once_sync(config: ExampleConfig, group: str, stage: list[str]) -> int:
    """Create one consumer, consume one batch, return the message count.

    ``stage`` is a one-element mutable tracker updated to ``"consume"`` once
    creation succeeds, so the caller can attribute failures to the right
    stage. The client is owned here and closed by ``with`` (HTTP resources
    only; this does not delete the server-side consumer or commit the batch).
    """
    with Client(config.app_key, config.app_secret, region=config.region_config) as client:
        created = client.message_subscriptions.create_consumer(group)
        consumer_id = _created_consumer_id(created)
        stage[0] = "consume"
        batch = client.message_subscriptions.consume(consumer_id)
        return _batch_message_count(batch)


async def _observe_once_async(config: ExampleConfig, group: str, stage: list[str]) -> int:
    """Async variant of :func:`_observe_once_sync`."""
    async with AsyncClient(
        config.app_key, config.app_secret, region=config.region_config
    ) as client:
        created = await client.message_subscriptions.create_consumer(group)
        consumer_id = _created_consumer_id(created)
        stage[0] = "consume"
        batch = await client.message_subscriptions.consume(consumer_id)
        return _batch_message_count(batch)


def main(argv: Sequence[str] | None = None) -> int:
    """One-shot observe: create one consumer, consume one batch, report.

    ``--mode`` is required; without ``--execute`` no environment is read and
    no client is created. Exit codes: ``0`` observed, ``1`` execution
    failure, ``2`` argument/configuration error, ``130`` interrupted.
    """
    mode: str | None = None
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentError:
        return _fail(None, "arguments", "input", 2)
    except SystemExit as exc:
        # argparse uses SystemExit for --help; it never reads config or
        # creates a client, so a clean help exit propagates unchanged.
        if exc.code == 0:
            raise
        return _fail(None, "arguments", "input", 2)

    mode = args.mode
    if not args.execute:
        return _fail(mode, "arguments", "input", 2)

    # stage[0] tracks arguments -> configuration -> create -> consume -> done.
    stage = ["configuration"]
    try:
        config, group = _load_configuration()
        stage[0] = "create"
        if mode == "sync":
            messages = _observe_once_sync(config, group, stage)
        else:
            messages = asyncio.run(_observe_once_async(config, group, stage))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return _fail(mode, stage[0], "interrupted", 130)
    except ConfigError:
        return _fail(mode, "configuration", "configuration", 2)
    except BaseException as exc:  # noqa: BLE001 - mapped to a fixed enum
        family = _error_family(exc, stage[0])
        code = 2 if family in ("input", "configuration") else 1
        return _fail(mode, stage[0], family, code)

    _emit(_success_report(mode, messages))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
