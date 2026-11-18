"""A04 proof: every frozen business entrypoint is really entered.

All 153 public paths run through their real method bodies (sync and async)
with decoded synthetic wire-case arguments while ``client.request`` is
replaced by a no-network stub returning ``ApiResponse``. Assertions use
recorded diagnostic events only - never source inspection.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

import ezviz_unified
from ezviz_unified import ApiResponse, AsyncClient, Client, RegionConfig
from ezviz_unified._diagnostic_registry import ENTRYPOINTS, OPERATIONS
from ezviz_unified.diagnostic_report import DiagnosticRecorder
from harness import wire_eval

ROOT = Path(__file__).resolve().parents[1]
COVERAGE = json.loads((ROOT / "docs" / "diagnostics-v1-coverage.json").read_text())
CASES = {
    case["operation_id"]: case
    for case in json.loads((ROOT / "evals" / "contracts" / "cases.json").read_text())["cases"]
}
BINDINGS = json.loads((ROOT / "contracts" / "sdk-bindings.json").read_text())["bindings"]
CONTRACTS = {
    contract["operation_id"]: contract
    for contract in json.loads(
        (ROOT / "contracts" / "reviewed-transport-contracts.json").read_text()
    )["contracts"]
}

ENTRIES = tuple(COVERAGE["business_entrypoints"])
ADDITIONAL = tuple(COVERAGE["additional_entrypoints"])
MODES = ("sync", "async")

ENTRY_OPERATION = {}
for _entry in ENTRIES:
    for _binding in BINDINGS:
        _op = _binding.get("operation_id")
        if _binding.get("public_entry") == _entry and _op in CASES and _op in CONTRACTS:
            ENTRY_OPERATION[_entry] = _op
            break


def _refuse(request):  # pragma: no cover - safety net
    raise AssertionError(f"unexpected network use: {request.method} {request.url}")


def _build(mode, recorder, region):
    kwargs = {
        "region": region,
        "clock": lambda: 1000.0,
        "auth_transport": httpx.MockTransport(_refuse),
        "transport_factory": lambda origin: httpx.MockTransport(_refuse),
        "diagnostic_recorder": recorder,
    }
    cls = Client if mode == "sync" else AsyncClient
    return cls("dg-key", "dg-secret", **kwargs)


def _resolve(client, entry):
    target = client
    for part in entry.split("."):
        target = getattr(target, part)
    return target


def _decode(contract, case):
    registry = wire_eval._dto_registry(ezviz_unified)
    context = wire_eval._context_for(contract)
    args = wire_eval._decode_value(case.get("args", []), context, registry)
    kwargs = wire_eval._decode_value(case.get("kwargs", {}), context, registry)
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        pytest.fail("wire case must decode to positional args and kwargs")
    return context, args, kwargs


def _field(event, name, default="none"):
    return event.get(name, default)


def test_registry_covers_frozen_lists():
    assert len(ENTRIES) == 153
    assert len(set(ENTRIES)) == 153
    assert frozenset(ENTRIES) | frozenset(ADDITIONAL) == ENTRYPOINTS
    assert frozenset(COVERAGE["operation_names"]) == OPERATIONS


def test_every_entrypoint_has_a_wire_case():
    missing = [entry for entry in ENTRIES if entry not in ENTRY_OPERATION]
    assert not missing, f"no decodable wire case for: {missing}"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entry", ENTRIES)
def test_business_entry_records_call_scope(entry, mode):
    contract = CONTRACTS[ENTRY_OPERATION[entry]]
    case = CASES[ENTRY_OPERATION[entry]]
    context, args, kwargs = _decode(contract, case)
    region = wire_eval._region_for(ezviz_unified, contract)
    recorder = DiagnosticRecorder()
    produced = []

    def _record(operation):
        response = ApiResponse(200, "200", operation=operation.name, data=case["data"])
        produced.append(response)
        return response

    if mode == "sync":
        client = _build(mode, recorder, region)
        client.request = lambda operation, **_kw: _record(operation)
        method = _resolve(client, entry)
        try:
            result = method(*args, **kwargs)
        finally:
            client.close()
    else:

        async def _run():
            client = _build(mode, recorder, region)

            async def stub(operation, **_kw):
                return _record(operation)

            client.request = stub
            method = _resolve(client, entry)
            try:
                return await method(*args, **kwargs)
            finally:
                await client.aclose()

        result = asyncio.run(_run())

    events = recorder.report()["events"]
    matched = [e for e in events if e["entrypoint"] == entry and e["kind"] == "call"]
    started = [e for e in matched if e["outcome"] == "started"]
    succeeded = [e for e in matched if e["outcome"] == "succeeded"]
    assert len(started) == 1, f"{entry}/{mode} did not emit exactly one started event"
    assert len(succeeded) == 1, f"{entry}/{mode} did not emit exactly one succeeded event"
    assert started[0]["span_id"] == succeeded[0]["span_id"]
    assert all(e["mode"] == mode for e in matched)
    assert all(e["region"] == contract["region"] for e in matched)
    assert all(e["operation"] in OPERATIONS or e["operation"] == "other" for e in matched)
    assert len(produced) == 1, f"{entry}/{mode} did not reach client.request exactly once"
    assert result is produced[0], f"{entry}/{mode} did not return the stub ApiResponse"
    serial = context.get("device_serial")
    assert serial is None or str(serial) not in json.dumps(recorder.report())


@pytest.mark.parametrize("mode", MODES)
def test_components_call_unknown_operation(mode):
    recorder = DiagnosticRecorder()

    async def _run():
        client = _build(mode, recorder, RegionConfig.china())

        def _never(operation, **_kw):  # pragma: no cover - must not run
            raise AssertionError("unknown operation must fail before request")

        async def _never_async(operation, **_kw):  # pragma: no cover
            raise AssertionError("unknown operation must fail before request")

        client.request = _never if mode == "sync" else _never_async
        try:
            if mode == "sync":
                client.components.call("dg003.unknown.component", "EVAL1234")
            else:
                await client.components.call("dg003.unknown.component", "EVAL1234")
        finally:
            if mode == "sync":
                client.close()
            else:
                await client.aclose()

    with pytest.raises((ValueError, LookupError)):
        asyncio.run(_run())

    events = recorder.report()["events"]
    matched = [e for e in events if e["entrypoint"] == "components.call"]
    assert {e["outcome"] for e in matched} == {"started", "failed"}
    assert all(e["operation"] == "other" for e in matched)
    failed = [e for e in matched if e["outcome"] == "failed"]
    assert all(_field(e, "error_family") == "validation" for e in failed)
