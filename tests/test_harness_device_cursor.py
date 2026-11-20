"""Page metadata regressions for the shared ``POST /api/lapp/device/list`` route.

``cn.devices.list_by_id`` and legacy ``cn.devices.list`` hit the same route;
only the cursor case fixtures here supply a root ``page`` in the response body;
legacy synthetic cases are unaffected.
"""

import copy
import json
from pathlib import Path

import httpx
import pytest

from harness import wire_eval

ROOT = Path(__file__).resolve().parents[1]
DEVICE_LIST = "/api/lapp/device/list"
NEW_OP = "cn.devices.list_by_id"
LEGACY_OP = "cn.devices.list"


def _inputs(operation_id):
    contracts = json.loads((ROOT / "contracts/reviewed-transport-contracts.json").read_text())
    bindings = json.loads((ROOT / "contracts/sdk-bindings.json").read_text())
    rows = json.loads((ROOT / "evals/contracts/cases.json").read_text())["cases"]
    selected = next(row for row in rows if row["operation_id"] == operation_id)
    return contracts, bindings, {"schema_version": 1, "cases": [selected]}


@pytest.fixture
def business_bodies(monkeypatch):
    """Patch the MockTransport class factory to record business responses."""
    captured = []
    original = httpx.MockTransport

    def record(request, response):
        if request.url.path == DEVICE_LIST:
            captured.append((response.status_code, response.json()))

    def factory(handler):
        def observe(request):
            response = handler(request)
            record(request, response)
            return response

        return original(observe)

    monkeypatch.setattr(httpx, "MockTransport", factory)
    return captured


@pytest.mark.parametrize("operation_id", [NEW_OP, LEGACY_OP])
def test_shared_route_page_contract(operation_id, business_bodies):
    contracts, bindings, suite = _inputs(operation_id)
    report = wire_eval.evaluate(contracts, bindings, suite, ROOT)
    assert report["passed"] is True
    assert len(report["results"]) == 8
    assert all(row["passed"] for row in report["results"])

    captured = business_bodies
    assert len(captured) == 10
    http_faults = [body for status, body in captured if status == 500]
    ok = [body for status, body in captured if status == 200]
    successes = [body for body in ok if body["code"] == "200"]
    auth_faults = [body for body in ok if body["code"] == "10002"]
    perm_faults = [body for body in ok if body["code"] == "10031"]
    assert (len(successes), len(auth_faults), len(perm_faults), len(http_faults)) == (4, 2, 2, 2)
    assert all(body["code"] == "200" for body in http_faults)

    canonical = suite["cases"][0]
    if operation_id == NEW_OP:
        for body in successes:
            assert body["page"] == canonical["response_page"]
            assert body["data"] == canonical["data"]
        for body in auth_faults + perm_faults + http_faults:
            assert "page" not in body
    else:
        assert all("page" not in body for _, body in captured)


def _corrupt(row, kind):
    row = copy.deepcopy(row)
    page = row.get("response_page")
    if kind == "missing_page":
        row.pop("response_page", None)
    elif kind == "legacy_page":
        row["response_page"] = {"total": 1, "size": 10, "nextId": "cursor-1"}
    elif kind == "missing_next_id":
        del page["nextId"]
    elif kind == "next_id_numeric":
        page["nextId"] = 42
    elif kind == "size_bool":
        page["size"] = True
    elif kind == "total_string":
        page["total"] = "9"
    else:  # pragma: no cover
        raise AssertionError(f"unknown corruption {kind!r}")
    return row


@pytest.mark.parametrize(
    ("operation_id", "kind"),
    [
        pytest.param(NEW_OP, "missing_page", id="new-missing-response_page"),
        pytest.param(LEGACY_OP, "legacy_page", id="legacy-given-response_page"),
        pytest.param(NEW_OP, "missing_next_id", id="new-missing-nextId"),
        pytest.param(NEW_OP, "next_id_numeric", id="new-nextId-numeric"),
        pytest.param(NEW_OP, "size_bool", id="new-size-bool"),
        pytest.param(NEW_OP, "total_string", id="new-total-string"),
    ],
)
def test_bad_page_metadata_rejected(operation_id, kind, business_bodies):
    contracts, bindings, suite = _inputs(operation_id)
    suite["cases"] = [_corrupt(suite["cases"][0], kind)]
    report = wire_eval.evaluate(contracts, bindings, suite, ROOT)
    assert report["passed"] is False
    assert report["results"], "vacuous rejection report"
    assert all(
        row["passed"] is False and row["problem"] == "case_invalid" for row in report["results"]
    )
    assert not business_bodies
