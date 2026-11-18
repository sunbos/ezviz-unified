"""Offline contract tests for tools.live.verify_traffic_usage (MockTransport only, no network)."""

import asyncio
import json
from urllib.parse import parse_qs

import httpx
import pytest

import tools.live.verify_traffic_usage as v
from tools.live.verify_diagnostics import _BoundaryRejection

CREDS = {"app_key": "PRIVATEKEY", "app_secret": "PRIVATESECRET"}
TOKEN = "/api/lapp/token/get"
OPS = ["traffic.account_summary", "traffic.account_details", "traffic.day_details"]
PATH = dict(
    zip(
        OPS,
        [
            "/api/lapp/traffic/user/total",
            "/api/lapp/traffic/user/detail",
            "/api/lapp/traffic/day/detail",
        ],
    )
)
DTYPE = dict(zip(OPS, ["object", "array", "array"]))
PROFILES = [(op, m) for op in OPS for m in ("sync", "async")]
DATA = {
    TOKEN: {
        "accessToken": "PRIVATETOKEN",
        "expireTime": 9999999999999,
        "areaDomain": "https://open.ys7.com",
    },
    PATH[OPS[0]]: {"totalFlow": 123456, "private": "PRIVATEROW"},
    PATH[OPS[1]]: [{"deviceSerial": "PRIVATESERIAL"}],
    PATH[OPS[2]]: [{"deviceSerial": "PRIVATESERIAL"}],
}
FORMS = {
    TOKEN: {"appKey", "appSecret"},
    PATH[OPS[0]]: {"accessToken"},
    PATH[OPS[1]]: {"accessToken", "pageStart", "pageSize"},
    PATH[OPS[2]]: {"accessToken", "pageStart", "pageSize"},
}
STATUSES = ["failed"] + ["skipped"] * 5
ROW_KEYS = {"operation", "mode", "status", "http_requests", "data_type"}
DETAIL_FIELDS = {"accessToken": ["PRIVATETOKEN"], "pageStart": ["0"], "pageSize": ["1"]}
CLI_USAGE = "usage: python -m tools.live.verify_traffic_usage [--help] --prompt\n"
CLI_REFUSED = "verify_traffic_usage: refused (interactive --prompt required)\n"
CLI_PASSED = "verify_traffic_usage: outcome passed\n"
CLI_FAILED = "verify_traffic_usage: outcome failed\n"


def _form(request):
    return parse_qs(request.content.decode())


def _strict(request):
    path = request.url.path
    ok = (
        request.method == "POST"
        and path in DATA
        and set(_form(request)) == FORMS[path]
        and "form-urlencoded" in request.headers.get("content-type", "")
    )
    return httpx.Response(
        200, json={"code": "200" if ok else "50000", "data": DATA[path] if ok else None}
    )


class _UnconsumedStream:
    """Proves guard.admit only inspects buffered content, never the stream."""

    def __init__(self, inner):
        self._inner = inner
        self.reads = 0

    def __iter__(self):
        self.reads += 1
        return iter(self._inner)


def _install(monkeypatch, handler=_strict):
    """Point both delegate factories at one recording MockTransport."""
    calls = []

    def wrap(request):
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(v, "_sync_delegate", lambda: httpx.MockTransport(wrap))
    monkeypatch.setattr(v, "_async_delegate", lambda: httpx.MockTransport(wrap))
    return calls


def _run(monkeypatch, handler=_strict):
    calls = _install(monkeypatch, handler)
    return v.verify(dict(CREDS)), calls


def _rows(receipt):
    assert receipt["schema_version"] == 1
    assert receipt["scope"] == "domestic_account_traffic"
    profiles = receipt["profiles"]
    assert len(profiles) == 6
    # Frozen contract keys: no aliases, no extras.
    assert all(set(row) == ROW_KEYS for row in profiles)
    return profiles


def _guard_request(path, fields):
    """A buffered form POST whose stream is wrapped to detect consumption."""
    request = httpx.Request("POST", "https://open.ys7.com" + path, data=fields)
    request.stream = _UnconsumedStream(request.stream)
    return request


def test_happy_path_six_profiles_pass_over_twelve_ordered_calls(monkeypatch):
    receipt, calls = _run(monkeypatch)
    assert len(calls) == receipt["http_requests"] == 12
    for i, (op, _mode) in enumerate(PROFILES):
        tok, biz = calls[2 * i], calls[2 * i + 1]
        for req in (tok, biz):
            assert req.url.scheme == "https" and req.url.host == "open.ys7.com"
            assert "device" not in req.url.path
        assert tok.url.path == TOKEN and biz.url.path == PATH[op]
        assert _form(tok) == {"appKey": ["PRIVATEKEY"], "appSecret": ["PRIVATESECRET"]}
        pages = {"pageStart": ["0"], "pageSize": ["1"]} if op != OPS[0] else {}
        assert _form(biz) == {"accessToken": ["PRIVATETOKEN"], **pages}
        assert not ({"startTime", "endTime", "date", "startDate", "endDate"} & set(_form(biz)))
    assert receipt["outcome"] == "passed"
    for row, (op, mode) in zip(_rows(receipt), PROFILES):
        assert (row["operation"], row["mode"], row["status"]) == (op, mode, "passed")
        assert row["http_requests"] == 2 and row["data_type"] == DTYPE[op]
    blob = json.dumps(receipt)
    for marker in ("PRIVATE", "123456", "totalFlow", "deviceSerial"):
        assert marker not in blob


def test_business_10002_fails_first_profile_and_skips_rest(monkeypatch):
    def handler(request):
        if request.url.path == TOKEN:
            return httpx.Response(200, json={"code": "200", "data": DATA[TOKEN]})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATEMSG", "data": None})

    receipt, calls = _run(monkeypatch, handler)
    assert len(calls) == receipt["http_requests"] == 2
    assert calls[0].url.path == TOKEN and calls[1].url.path == PATH[OPS[0]]
    rows = _rows(receipt)
    assert [r["status"] for r in rows] == STATUSES
    assert rows[0]["http_requests"] == 2 and rows[0]["data_type"] is None
    assert all(r["http_requests"] == 0 and r["data_type"] is None for r in rows[1:])
    assert "PRIVATE" not in json.dumps(receipt)


def test_second_token_fetch_is_never_admitted_after_business_10002(monkeypatch):
    """A 10002 token-refresh attempt must be refused by the guard, not replayed."""

    def handler(request):
        if request.url.path == TOKEN:
            return httpx.Response(200, json={"code": "200", "data": DATA[TOKEN]})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATEMSG", "data": None})

    receipt, calls = _run(monkeypatch, handler)
    # Exactly one auth POST and one business POST ever reached the wire.
    assert len(calls) == 2
    assert [c.url.path for c in calls] == [TOKEN, PATH[OPS[0]]]
    assert sum(c.url.path == TOKEN for c in calls) == 1
    assert receipt["outcome"] == "failed"
    assert _rows(receipt)[0]["status"] == "failed"


def test_foreign_area_domain_yields_zero_business_requests(monkeypatch):
    """A token whose areaDomain is off-host stops before any business call."""

    def handler(request):
        assert request.url.path == TOKEN
        bad = dict(DATA[TOKEN], areaDomain="https://attacker.invalid")
        return httpx.Response(200, json={"code": "200", "data": bad})

    receipt, calls = _run(monkeypatch, handler)
    assert len(calls) == receipt["http_requests"] == 1
    assert calls[0].url.path == TOKEN
    assert [r["status"] for r in _rows(receipt)] == STATUSES
    assert "attacker" not in json.dumps(receipt)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://attacker.invalid/x"}),
        httpx.Response(200, json={"code": "10001", "msg": "PRIVATEAUTH", "data": None}),
    ],
)
def test_token_stage_faults_stop_after_one_real_request(monkeypatch, response):
    receipt, calls = _run(monkeypatch, lambda request: response)
    assert len(calls) == 1 and calls[0].url.path == TOKEN
    assert receipt["http_requests"] == 1
    assert [r["status"] for r in _rows(receipt)] == STATUSES
    blob = json.dumps(receipt)
    assert "PRIVATE" not in blob and "attacker" not in blob


def test_wrong_data_shape_marks_profile_failed(monkeypatch):
    def handler(request):
        data = DATA[TOKEN] if request.url.path == TOKEN else [{"shape": "PRIVATEBAD"}]
        return httpx.Response(200, json={"code": "200", "data": data})

    receipt, calls = _run(monkeypatch, handler)
    assert len(calls) == 2
    row = _rows(receipt)[0]
    assert row["status"] == "failed" and row["http_requests"] == 2
    assert row["data_type"] is None
    assert [r["status"] for r in receipt["profiles"][1:]] == ["skipped"] * 5
    assert "PRIVATEBAD" not in json.dumps(receipt)


def test_help_runs_preflight_without_credentials(capsys):
    assert v.main(["--help"]) == 0
    out = capsys.readouterr()
    assert out.out == CLI_USAGE and out.err == ""
    assert "PRIVATE" not in out.out + out.err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--bogus"],
        ["--prompt", "--app-key", "PRIVATEKEY"],
        ["--prompt", "--app-secret", "PRIVATESECRET"],
        ["--prompt", "--token", "PRIVATETOKEN"],
        ["--prompt", "extra"],
    ],
)
def test_secret_like_argv_is_rejected(argv, capsys):
    assert v.main(argv) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == CLI_REFUSED
    assert "PRIVATE" not in out.out + out.err


def _expected_receipt():
    return {
        "schema_version": 1,
        "scope": "domestic_account_traffic",
        "outcome": "passed",
        "http_requests": 12,
        "profiles": [
            {
                "operation": op,
                "mode": mode,
                "status": "passed",
                "http_requests": 2,
                "data_type": DTYPE[op],
            }
            for op, mode in PROFILES
        ],
    }


def _prompt_stubbed_credentials(monkeypatch):
    """The real isatty gate stays; only the credential source is stubbed."""
    monkeypatch.setattr(v, "_read_credentials", lambda: dict(CREDS))


def test_prompt_run_emits_fixed_report_after_real_preflight(monkeypatch, capsys):
    calls = _install(monkeypatch)
    # One --prompt run is the only driver: the synthetic delegate records
    # the real request budget with no separate verify() pass.
    _prompt_stubbed_credentials(monkeypatch)
    assert len(calls) == 0
    assert v.main(["--prompt"]) == 0
    assert len(calls) == 12
    out = capsys.readouterr()
    assert out.out == json.dumps(_expected_receipt()) + "\n"
    assert out.err == CLI_PASSED
    for marker in ("PRIVATEKEY", "PRIVATESECRET", "PRIVATETOKEN", "PRIVATESERIAL", "123456"):
        assert marker not in out.out + out.err


def test_prompt_reports_failed_run_without_leaking(monkeypatch, capsys):
    def handler(request):
        if request.url.path == TOKEN:
            return httpx.Response(200, json={"code": "200", "data": DATA[TOKEN]})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATEMSG", "data": None})

    _install(monkeypatch, handler)
    _prompt_stubbed_credentials(monkeypatch)
    assert v.main(["--prompt"]) == 1
    out = capsys.readouterr()
    receipt = json.loads(out.out)
    assert receipt["outcome"] == "failed" and receipt["http_requests"] == 2
    assert out.err == CLI_FAILED
    for marker in ("PRIVATEKEY", "PRIVATESECRET", "PRIVATETOKEN", "PRIVATEMSG"):
        assert marker not in out.out + out.err


def test_prompt_preflight_failure_is_not_a_successful_prompt(monkeypatch, capsys):
    """A failed preflight must exit non-zero; --prompt is not a pass override."""

    def handler(request):
        if request.url.path == TOKEN:
            return httpx.Response(200, json={"code": "200", "data": DATA[TOKEN]})
        return httpx.Response(200, json={"code": "10002", "msg": "PRIVATEMSG", "data": None})

    _install(monkeypatch, handler)
    preflight = v.verify(dict(CREDS))
    assert preflight["outcome"] == "failed"
    _prompt_stubbed_credentials(monkeypatch)
    assert v.main(["--prompt"]) == 1  # not 0: failure is not masked as success
    out = capsys.readouterr()
    assert out.err == CLI_FAILED
    assert "PRIVATE" not in out.out + out.err


def test_wire_contract_allows_no_extra_route_field_time_or_page(monkeypatch):
    receipt, calls = _run(monkeypatch)  # strict transport refuses off-contract wires
    assert receipt["outcome"] == "passed" and len(calls) == 12
    forbidden = {
        "deviceSerial",
        "startTime",
        "endTime",
        "startDate",
        "endDate",
        "date",
        "channelNo",
        "pageIndex",
    }
    assert all(req.url.path in FORMS and not (forbidden & set(_form(req))) for req in calls)


class TestGuardAdmit:
    """Direct _Guard.admit boundary tests: order, fields, latch, no stream reads."""

    def _new_guard(self, op="traffic.account_details"):
        return v._Guard(op, "PRIVATEKEY", "PRIVATESECRET")

    def _auth_ok(self, guard):
        guard.admit(_guard_request(TOKEN, {"appKey": "PRIVATEKEY", "appSecret": "PRIVATESECRET"}))

    def _valid_business(self, op):
        fields = (
            {"accessToken": "PRIVATETOKEN"}
            if op == "traffic.account_summary"
            else dict(DETAIL_FIELDS)
        )
        return _guard_request(PATH[op], fields)

    def test_admits_valid_auth_then_business_in_order(self):
        for op in OPS:
            guard = self._new_guard(op)
            self._auth_ok(guard)
            assert guard.spent == 1 and not guard.denied
            guard.admit(self._valid_business(op))
            assert guard.spent == 2 and not guard.denied

    def test_first_request_on_auth_path_with_wrong_fields_fails(self):
        guard = self._new_guard()
        with pytest.raises(_BoundaryRejection):
            guard.admit(_guard_request(TOKEN, {"appKey": "PRIVATEKEY", "appSecret": "WRONG"}))
        assert guard.denied and guard.spent == 0

    def test_first_request_on_wrong_path_fails(self):
        guard = self._new_guard()
        with pytest.raises(_BoundaryRejection):
            guard.admit(
                _guard_request(
                    "/api/lapp/device/list", {"appKey": "PRIVATEKEY", "appSecret": "PRIVATESECRET"}
                )
            )
        assert guard.denied and guard.spent == 0

    @pytest.mark.parametrize(
        "path,fields",
        [
            # Another business route is never admitted even with exact detail fields.
            ("/api/lapp/traffic/day/detail", dict(DETAIL_FIELDS)),
            # The unauthorized device path is refused outright.
            (
                "/api/lapp/traffic/device/detail",
                {**DETAIL_FIELDS, "deviceSerial": ["PRIVATESERIAL"]},
            ),
            # A wrong page position violates the frozen first-page contract.
            (
                "/api/lapp/traffic/user/detail",
                {"accessToken": ["PRIVATETOKEN"], "pageStart": ["1"], "pageSize": ["1"]},
            ),
            # Date filters are outside this account-read contract.
            (
                "/api/lapp/traffic/user/detail",
                {**DETAIL_FIELDS, "startTime": ["1"], "endTime": ["2"]},
            ),
            # A duplicated auth POST is not a second admission.
            (TOKEN, {"appKey": "PRIVATEKEY", "appSecret": "PRIVATESECRET"}),
        ],
    )
    def test_out_of_contract_business_is_refused_and_latched(self, path, fields):
        guard = self._new_guard("traffic.account_details")
        self._auth_ok(guard)
        with pytest.raises(_BoundaryRejection):
            guard.admit(_guard_request(path, fields))
        assert guard.denied and guard.spent == 1
        # Once latched, even a perfectly valid request is refused.
        with pytest.raises(_BoundaryRejection):
            guard.admit(self._valid_business("traffic.account_details"))
        assert guard.spent == 1

    @pytest.mark.parametrize(
        "path,fields",
        [
            # Query strings are outside the admitted origin contract.
            ("/api/lapp/traffic/user/detail?accessToken=x", dict(DETAIL_FIELDS)),
        ],
    )
    def test_query_bearing_business_is_refused(self, path, fields):
        guard = self._new_guard("traffic.account_details")
        self._auth_ok(guard)
        with pytest.raises(_BoundaryRejection):
            guard.admit(_guard_request(path, fields))
        assert guard.denied and guard.spent == 1

    def test_streaming_body_is_not_consumed_by_guard(self):
        guard = self._new_guard()
        request = _guard_request(TOKEN, {"appKey": "PRIVATEKEY", "appSecret": "PRIVATESECRET"})
        guard.admit(request)
        assert request.stream.reads == 0  # guard parsed buffered content only


CLI_ABORTED = "verify_traffic_usage: aborted\n"
# (interrupt kind, admitted requests before the interrupt, index of the
# interrupted profile, expected terminal statuses). The sync interrupt lands
# on profile 0's business POST; the async cancellation lands on profile 1's
# business POST after profile 0 passed cleanly.
_INTERRUPT_CASES = [
    ("keyboard", 2, 0, ["failed"] + ["skipped"] * 5),
    ("cancelled", 4, 1, ["passed", "failed"] + ["skipped"] * 4),
]


@pytest.mark.parametrize(
    "kind,budget,interrupted,statuses",
    _INTERRUPT_CASES,
    ids=["keyboard-on-second-request", "cancelled-on-fourth-request"],
)
def test_mid_run_interruption_reports_actual_requests_and_skips_rest(
    monkeypatch, capsys, kind, budget, interrupted, statuses
):
    """An interrupted run still prints the safe failed receipt and exits 130."""
    seen = []
    real = _strict  # spy on the actual contract handler, not a stub

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == budget:
            if kind == "keyboard":
                raise KeyboardInterrupt("PRIVATE")
            raise asyncio.CancelledError("PRIVATE")
        return real(request)

    calls = _install(monkeypatch, handler)
    _prompt_stubbed_credentials(monkeypatch)
    assert v.main(["--prompt"]) == 130
    out = capsys.readouterr()
    r = json.loads(out.out)
    assert r["scope"] == "domestic_account_traffic"
    assert r["outcome"] == "failed"
    assert r["http_requests"] == budget
    rows = r["profiles"]
    assert [row["status"] for row in rows] == statuses
    assert rows[interrupted]["http_requests"] == 2
    assert rows[interrupted]["data_type"] is None
    assert all(
        row["http_requests"] == 0 and row["data_type"] is None for row in rows[interrupted + 1 :]
    )
    assert [(row["operation"], row["mode"]) for row in rows] == PROFILES
    assert out.err == CLI_ABORTED
    # The wire saw exactly the real admitted paths up to the interrupt:
    # TOKEN+business pairs per completed profile, then the interrupted
    # profile's TOKEN+business pair, in frozen order.
    assert len(calls) == len(seen) == budget
    expected = []
    for i in range(interrupted):
        expected += [TOKEN, PATH[PROFILES[i][0]]]
    expected += [TOKEN, PATH[PROFILES[interrupted][0]]]
    assert seen == expected
    assert "PRIVATE" not in out.out + out.err


@pytest.mark.parametrize(
    "kind,budget,interrupted,statuses",
    _INTERRUPT_CASES,
    ids=["keyboard-on-second-request", "cancelled-on-fourth-request"],
)
def test_verify_receipt_in_progress_interrupt_keeps_honest_counts(
    monkeypatch, kind, budget, interrupted, statuses
):
    """``verify`` propagates the interrupt instead of fabricating a receipt.

    A BaseException escapes the per-profile ``except Exception`` before a
    receipt can be assembled, so no row is ever rewritten to ``passed`` and
    no invented count is emitted. The admitted-path prefix proves the honest
    per-profile accounting the partial receipt would have carried: each
    earlier profile spent its full 2 requests, and the interrupted profile
    stopped at its real ``guard.spent``.
    """
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if len(seen) == budget:
            if kind == "keyboard":
                raise KeyboardInterrupt("PRIVATE")
            raise asyncio.CancelledError("PRIVATE")
        return _strict(request)

    calls = _install(monkeypatch, handler)
    with pytest.raises((KeyboardInterrupt, asyncio.CancelledError)):
        v.verify(dict(CREDS))
    # Real admitted paths only; the total is the honest spent count, and
    # each completed profile admitted TOKEN then its own business path
    # before the interrupted profile admitted its own pair and stopped.
    assert len(calls) == len(seen) == budget
    expected = []
    for i in range(interrupted):
        expected += [TOKEN, PATH[PROFILES[i][0]]]
    expected += [TOKEN, PATH[PROFILES[interrupted][0]]]
    assert seen == expected
    assert statuses == (
        ["failed"] + ["skipped"] * 5 if interrupted == 0 else ["passed", "failed"] + ["skipped"] * 4
    )


def test_prompt_credential_interrupt_aborts_with_zero_requests(monkeypatch, capsys):
    """An interrupt at the hidden credential prompt never reaches the wire.

    ``_read_credentials`` raises before any client, guard, or transport is
    built, so zero live requests are admitted and no public execution
    receipt is emitted — only the fixed abort line and exit 130.
    """
    calls = _install(monkeypatch)

    def interrupted():
        raise KeyboardInterrupt("PRIVATE")

    monkeypatch.setattr(v, "_read_credentials", interrupted)
    assert v.main(["--prompt"]) == 130
    out = capsys.readouterr()
    assert out.out == "" and out.err == CLI_ABORTED
    assert len(calls) == 0
    assert "PRIVATE" not in out.out + out.err


def test_prompt_credential_cancelled_aborts_with_zero_requests(monkeypatch, capsys):
    """A cancellation at the credential prompt is also a clean abort."""
    calls = _install(monkeypatch)

    def cancelled():
        raise asyncio.CancelledError("PRIVATE")

    monkeypatch.setattr(v, "_read_credentials", cancelled)
    assert v.main(["--prompt"]) == 130
    out = capsys.readouterr()
    assert out.out == "" and out.err == CLI_ABORTED
    assert len(calls) == 0
    assert "PRIVATE" not in out.out + out.err
