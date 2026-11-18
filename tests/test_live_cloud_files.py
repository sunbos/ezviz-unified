"""Core unit tests for tools/live/verify_cloud_files.py (fully mocked HTTP)."""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified.errors import (
    ApiError,
    AuthenticationError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)
from tools.live import verify_cloud_files as probe

AUTH = "/api/lapp/token/get"
SPACES = "/api/service/open/storage/engine/space/listById"
VOD = "/api/service/open/vod/files"
KEY, SECRET, TOKEN = "KK_3f1a9c", "SS_8d2b7e", "TT_5c4d9a1f"
NAME, NODE, PURL = "NN_71e3", "ND_86bd", "https://files.example.invalid/x"
SID = 12345
CFG = {"app_key": KEY, "app_secret": SECRET}
SECRETS = (KEY, SECRET, TOKEN, NAME, NODE, PURL, "files.example.invalid", str(SID))
SYNTHETIC_SECRET_TEXT = "synthetic_secret_" + "Ab3dEf" * 64


def J(p):
    return httpx.Response(200, json=p)


def AUTHJ():
    return J({"code": "200", "data": {"accessToken": TOKEN, "expireTime": 9999999999000}})


def SPJ(r):
    return J({"meta": {"code": 200}, "data": {"result": r, "hasNext": False}})


def VJ(d):
    return J({"meta": {"code": 200}, "data": d})


ROW = {"spaceId": SID, "bizType": "vod", "namespaceType": 1, "spaceName": NAME}
BADROWS = [
    True,
    "s",
    {"spaceId": "1", "bizType": "vod", "namespaceType": 1},
    {"spaceId": 9, "bizType": "x", "namespaceType": 1},
    {"spaceId": 8, "bizType": "vod", "namespaceType": 2},
]


def runv(monkeypatch, script):
    seen, made = [], []

    def h(req):
        seen.append(req)
        o = script[req.url.path].pop(0)
        if isinstance(o, Exception):
            raise o
        return o()

    monkeypatch.setattr(
        probe, "_new_inner_transport", lambda: made.append(httpx.MockTransport(h)) or made[-1]
    )
    return probe.verify(dict(CFG)), seen


def test_verify_success_and_privacy(monkeypatch):
    script = {
        AUTH: [AUTHJ],
        SPACES: [lambda: SPJ(BADROWS + [ROW])],
        VOD: [lambda: VJ([{"folderNode": NODE, "coverPicUrl": PURL, "fileSize": 9}])],
    }
    rep, seen = runv(monkeypatch, script)
    assert rep["outcome"] == "success" and rep["reason"] == "ok" and rep["stage"] == "complete"
    assert rep["http_requests"] == 3 and rep["logical_calls"] == 2
    assert rep["route_counts"] == {"authentication": 1, "spaces": 1, "vod": 1}
    assert rep["space_page_count"] == 6 and rep["space_has_next"] is False
    assert rep["ordinary_vod_candidate_found"] is True and rep["vod_data_type"] == "array"
    assert rep["vod_item_count"] == 1
    assert rep["known_field_types"] == {
        "folderNode": ["string"],
        "coverPicUrl": ["string"],
        "fileSize": ["integer"],
    }
    assert probe._report_allowed(rep) is True
    assert [r.url.path for r in seen] == [AUTH, SPACES, VOD]
    a, s, v = seen
    assert (
        a.method == "POST" and a.url.params.multi_items() == [] and "accessToken" not in a.headers
    )
    assert parse_qs(a.read().decode(), strict_parsing=True) == {
        "appKey": [KEY],
        "appSecret": [SECRET],
    }
    assert s.method == "GET" and s.headers["accessToken"] == TOKEN and not s.read()
    sq = dict(s.url.params.multi_items())
    assert set(sq) == {"startTime", "endTime", "pageSize", "bizTypeList"}
    assert sq["pageSize"] == "50" and sq["bizTypeList"] == "vod"
    assert (sq["startTime"], sq["endTime"]) == (rep["window_start"], rep["window_end"])
    f = "%Y-%m-%d %H:%M:%S"
    assert datetime.strptime(rep["window_end"], f) - datetime.strptime(
        rep["window_start"], f
    ) == timedelta(days=30)
    assert v.method == "GET" and v.headers["accessToken"] == TOKEN and not v.read()
    assert dict(v.url.params.multi_items()) == {"spaceId": str(SID)}
    blob = json.dumps(rep, ensure_ascii=False, sort_keys=True)
    for x in SECRETS:
        assert x not in blob
    for k in ("appKey", "appSecret", "accessToken", "spaceId", "spaceName"):
        assert k not in blob


def test_verify_uses_existing_retry_budget(monkeypatch):
    def bad():
        return J({"meta": {"code": 10002}, "data": None})

    script = {AUTH: [AUTHJ] * 3, SPACES: [bad, lambda: SPJ([ROW])], VOD: [bad, lambda: VJ([])]}
    rep, seen = runv(monkeypatch, script)
    assert rep["outcome"] == "success" and rep["http_requests"] == 7
    assert rep["route_counts"] == {"authentication": 3, "spaces": 2, "vod": 2}
    assert [r.url.path for r in seen] == [AUTH, SPACES, AUTH, SPACES, VOD, AUTH, VOD]
    w = (rep["window_start"], rep["window_end"])
    for r in seen:
        q = dict(r.url.params.multi_items())
        if r.url.path == SPACES:
            assert (q["startTime"], q["endTime"]) == w
        if r.url.path == VOD:
            assert q["spaceId"] == str(SID)


SHAPE = {
    "empty": SPJ([]),
    "data_not_dict": J({"meta": {"code": 200}, "data": [1]}),
    "result_not_list": J({"meta": {"code": 200}, "data": {"result": {}}}),
    "over_50": SPJ([ROW] * 51),
    "no_candidate": SPJ(BADROWS),
}


@pytest.mark.parametrize("case", sorted(SHAPE))
def test_verify_stops_without_resource(monkeypatch, case):
    rep, seen = runv(monkeypatch, {AUTH: [AUTHJ], SPACES: [lambda: SHAPE[case]]})
    assert rep["outcome"] == "not_verified"
    assert [r.url.path for r in seen] == [AUTH, SPACES]
    assert rep["route_counts"]["vod"] == 0 and rep["logical_calls"] == 1
    assert "vod_data_type" not in rep and "known_field_types" not in rep
    assert probe._report_allowed(rep) is True


def test_verify_vod_non_array_not_verified(monkeypatch):
    rep, _ = runv(
        monkeypatch, {AUTH: [AUTHJ], SPACES: [lambda: SPJ([ROW])], VOD: [lambda: VJ({"a": 1})]}
    )
    assert rep["outcome"] == "not_verified" and rep["reason"] == "unexpected_vod_data_shape"
    assert rep["vod_data_type"] == "object" and rep["vod_item_count"] is None
    assert probe._report_allowed(rep) is True


def test_verify_vod_empty_array_success(monkeypatch):
    rep, _ = runv(monkeypatch, {AUTH: [AUTHJ], SPACES: [lambda: SPJ([ROW])], VOD: [lambda: VJ([])]})
    assert rep["outcome"] == "success" and rep["vod_item_count"] == 0
    assert rep["known_field_types"] == {} and probe._report_allowed(rep) is True


def test_verify_space_page_missing_data_not_verified(monkeypatch):
    rep, seen = runv(monkeypatch, {AUTH: [AUTHJ], SPACES: [lambda: J({"meta": {"code": 200}})]})
    assert rep["outcome"] == "not_verified" and rep["reason"] == "unexpected_space_page_shape"
    assert [r.url.path for r in seen] == [AUTH, SPACES]
    assert rep["route_counts"] == {"authentication": 1, "spaces": 1, "vod": 0}
    assert rep["http_requests"] == 2 and rep["logical_calls"] == 1
    assert "vod_data_type" not in rep and "known_field_types" not in rep
    assert probe._report_allowed(rep) is True


FAIL = {
    "perm": (
        {AUTH: [lambda: J({"code": "10031", "data": None})]},
        "permission",
        "permission_denied",
        [AUTH],
        {"authentication": 1, "spaces": 0, "vod": 0},
    ),
    "auth": (
        {AUTH: [lambda: J({"code": "10002", "data": None})]},
        "authentication",
        "authentication_error",
        [AUTH],
        {"authentication": 1, "spaces": 0, "vod": 0},
    ),
    "api": (
        {AUTH: [AUTHJ], SPACES: [lambda: J({"meta": {"code": 49999}, "data": None})]},
        "api",
        "api_error",
        [AUTH, SPACES],
        {"authentication": 1, "spaces": 1, "vod": 0},
    ),
    "transport": (
        {AUTH: [httpx.ConnectError("d")]},
        "transport",
        "transport_error",
        [AUTH],
        {"authentication": 1, "spaces": 0, "vod": 0},
    ),
    "unknown": (
        {AUTH: [AUTHJ], SPACES: [RuntimeError("SYNTHETIC_UNKNOWN_ERROR_SECRET")]},
        "unknown",
        "unknown_error",
        [AUTH, SPACES],
        {"authentication": 1, "spaces": 1, "vod": 0},
    ),
}


@pytest.mark.parametrize("case", sorted(FAIL))
def test_verify_failure_paths(monkeypatch, case):
    script, family, reason, paths, counts = FAIL[case]
    rep, seen = runv(monkeypatch, copy.deepcopy(script))
    assert rep["outcome"] == "failed" and rep["error_family"] == family and rep["reason"] == reason
    assert [r.url.path for r in seen] == paths
    assert rep["route_counts"] == counts
    assert rep["http_requests"] == len(paths) == sum(counts.values())
    assert probe._report_allowed(rep) is True
    blob = json.dumps(rep, ensure_ascii=False)
    for x in SECRETS:
        assert x not in blob
    assert "SYNTHETIC_UNKNOWN_ERROR_SECRET" not in blob


class _FailClose(httpx.MockTransport):
    def close(self):
        super().close()
        raise RuntimeError("close exploded")


@pytest.mark.parametrize("tail", ["success", "empty", "nocand"])
def test_verify_close_failure_cannot_preserve_success(monkeypatch, tail):
    s = {AUTH: [AUTHJ], SPACES: [lambda: SPJ([ROW] if tail != "nocand" else BADROWS)]}
    if tail == "success":
        s[VOD] = [lambda: VJ([])]
    elif tail == "empty":
        s[SPACES] = [lambda: SPJ([])]

    def h(req):
        o = s[req.url.path].pop(0)
        return o()

    monkeypatch.setattr(probe, "_new_inner_transport", lambda: _FailClose(h))
    rep = probe.verify(dict(CFG))
    assert rep["outcome"] == "failed" and rep["reason"] == "close_failure"
    assert probe._report_allowed(rep) is True


W = ("2026-10-19 08:00:00", "2026-11-18 08:00:00")
QW = (
    "startTime="
    + W[0].replace(" ", "%20")
    + "&endTime="
    + W[1].replace(" ", "%20")
    + "&pageSize=50&bizTypeList=vod"
)


class _Cnt(httpx.BaseTransport):
    def __init__(self):
        self.calls = 0

    def handle_request(self, req):
        self.calls += 1
        return J({"meta": {"code": 200}, "data": {}})


def req(method, path, query="", headers=None, content=None):
    h = (
        {"accessToken": TOKEN}
        if method == "GET"
        else {"Content-Type": "application/x-www-form-urlencoded"}
    )
    h.update(headers or {})
    return httpx.Request(method, "https://open.ys7.com" + path + query, headers=h, content=content)


def areq(**kw):
    url = kw.pop("url", "https://open.ys7.com" + AUTH)
    return httpx.Request(
        kw.pop("method", "POST"),
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        content=kw.pop("content", b"appKey=K&appSecret=S"),
    )


BADREQ = {
    "http": areq(url="http://open.ys7.com" + AUTH),
    "host": areq(url="https://open.e.com" + AUTH),
    "userinfo": areq(url="https://u:p@open.ys7.com" + AUTH),
    "port": areq(url="https://open.ys7.com:8443" + AUTH),
    "frag": areq(url="https://open.ys7.com" + AUTH + "#f"),
    "route": areq(url="https://open.ys7.com/api/x"),
    "method": areq(method="GET"),
    "extra_q": req("GET", SPACES, "?" + QW + "&e=1"),
    "dup_q": req(
        "GET",
        SPACES,
        "?pageSize=50&pageSize=50&"
        + "startTime="
        + W[0].replace(" ", "%20")
        + "&endTime="
        + W[1].replace(" ", "%20")
        + "&bizTypeList=vod",
    ),
    "get_body": req("GET", VOD, "?spaceId=" + str(SID), content=b"x=1"),
    "tok_query": req("GET", SPACES, "?" + QW + "&accessToken=" + TOKEN, headers={}),
    "window": req(
        "GET",
        SPACES,
        "?startTime=2026-10-01%2000:00:00&endTime="
        + W[1].replace(" ", "%20")
        + "&pageSize=50&bizTypeList=vod",
    ),
    "vod_unbound": req("GET", VOD, "?spaceId=" + str(SID)),
    "vod_wrong": req("GET", VOD, "?spaceId=99999"),
}


@pytest.mark.parametrize("case", sorted(BADREQ))
def test_guard_rejects_unfrozen_requests(case):
    b = probe._RequestBudget(window=W)
    if case == "vod_wrong":
        b.bind_space(SID)
    d = _Cnt()
    t = probe._GuardedTransport(d, b, deadline=None)
    with pytest.raises(probe._BoundaryRejection):
        t.handle_request(BADREQ[case])
    assert d.calls == 0 and b.total == 0


def test_budget_limits():
    b = probe._RequestBudget(window=W)
    d = _Cnt()
    t = probe._GuardedTransport(d, b, deadline=None)
    for _ in range(3):
        t.handle_request(areq())
    b.bind_space(SID)
    for _ in range(2):
        t.handle_request(req("GET", SPACES, "?" + QW))
    for _ in range(2):
        t.handle_request(req("GET", VOD, "?spaceId=" + str(SID)))
    assert b.total == 7 and d.calls == 7
    assert b.route_counts() == {"authentication": 3, "spaces": 2, "vod": 2}
    for r in (areq(), req("GET", SPACES, "?" + QW), req("GET", VOD, "?spaceId=" + str(SID))):
        with pytest.raises(probe._BoundaryRejection):
            t.handle_request(r)
    assert b.rejected == 3 and b.total == 7 and d.calls == 7


def test_budget_deadline_before_send():
    b = probe._RequestBudget(window=W)
    d = _Cnt()
    t = probe._GuardedTransport(d, b, deadline=0.0)
    with pytest.raises(probe._BoundaryRejection):
        t.handle_request(areq())
    assert d.calls == 0 and b.total == 0


class _Stream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self._c = chunks
        self.n = 0
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.n >= len(self._c):
            raise StopIteration
        c = self._c[self.n]
        self.n += 1
        return c

    def close(self):
        self.closed = True


def sresp(chunks, headers=None):
    r = httpx.Request("GET", "https://open.ys7.com/")
    s = _Stream(chunks)
    return r, httpx.Response(200, headers=headers or {}, stream=s, request=r), s


MIB = 1024 * 1024


def test_bounded_response_exact_cap():
    r, resp, s = sresp([b"x" * MIB])
    out = probe._bounded_response(r, resp, deadline=None)
    assert out.read() == b"x" * MIB and s.closed and s.n == 1


def test_bounded_response_over_cap_no_consume():
    r, resp, s = sresp([b"x" * MIB, b"y", b"z"])
    with pytest.raises(probe._BoundaryRejection):
        probe._bounded_response(r, resp, deadline=None)
    assert s.closed and s.n <= 2


def test_bounded_response_gzip_rejected():
    r, resp, s = sresp([b"d"], {"Content-Encoding": "gzip"})
    with pytest.raises(probe._BoundaryRejection):
        probe._bounded_response(r, resp, deadline=None)
    assert s.closed and s.n == 0


def test_bounded_response_deadline_between_chunks():
    r, resp, s = sresp([b"a", b"b"])
    with pytest.raises(probe._BoundaryRejection):
        probe._bounded_response(r, resp, deadline=time.monotonic() - 1)
    assert s.closed


FAM = [
    (AuthenticationError("x", code=10002), "authentication"),
    (PermissionDeniedError("x"), "permission"),
    (ApiError("x"), "api"),
    (probe._BoundaryRejection("x"), "protocol"),
    (ProtocolError("x"), "protocol"),
    (TransportError("x", reason="t"), "transport"),
    (httpx.ConnectError("x"), "transport"),
    (ValueError("x"), "unknown"),
    (RuntimeError("AuthenticationError"), "unknown"),
]


@pytest.mark.parametrize("exc,exp", FAM)
def test_error_families(exc, exp):
    assert probe._error_family(exc) == exp


def vrep():
    return {
        "schema_version": 1,
        "scope": probe._SCOPE,
        "region": "domestic",
        "recorded_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "window_start": W[0],
        "window_end": W[1],
        "window_basis": "createTime",
        "window_timezone_assumption": "Asia/Shanghai_wall_time",
        "max_http_requests": 7,
        "http_requests": 3,
        "logical_calls": 2,
        "route_counts": {"authentication": 1, "spaces": 1, "vod": 1},
        "stage": "complete",
        "outcome": "success",
        "reason": "ok",
        "space_page_count": 1,
        "space_has_next": False,
        "ordinary_vod_candidate_found": True,
        "vod_data_type": "array",
        "vod_item_count": 0,
        "known_field_types": {"fileSize": ["integer"]},
        "error_family": "none",
        "cloud_recording_v1_discovery": "not_available_in_current_sdk",
    }


def tampered():
    b = vrep()
    c = {}

    def k(n, f):
        r = copy.deepcopy(b)
        f(r)
        c[n] = r

    k("tz", lambda r: r.update(recorded_at_utc="2026-11-18T08:00:00+08:00"))
    k("w30", lambda r: r.update(window_start="2026-10-20 08:00:00"))
    k("ts_secret", lambda r: r.update(recorded_at_utc=SYNTHETIC_SECRET_TEXT))
    k("ws_secret", lambda r: r.update(window_start=SYNTHETIC_SECRET_TEXT))
    k("we_secret", lambda r: r.update(window_end=SYNTHETIC_SECRET_TEXT))
    k("cap", lambda r: r["route_counts"].update(authentication=4))
    k("sum", lambda r: r.update(http_requests=2))
    k("key", lambda r: r.update(evil="x"))
    k("enum", lambda r: r.update(outcome="x"))
    k("bucket", lambda r: r["known_field_types"].update(fileSize=["x"]))
    k("field", lambda r: r["known_field_types"].update(z=["string"]))
    k("bool", lambda r: r.update(space_has_next="y"))
    k("schema", lambda r: r.update(schema_version=2))
    k("vtype", lambda r: r.update(vod_data_type="dict"))
    k("page", lambda r: r.update(space_page_count=51))
    k("miss", lambda r: r.pop("outcome"))
    k("lc", lambda r: r.update(logical_calls=3))
    return c


@pytest.mark.parametrize("case", sorted(tampered()))
def test_report_allowlist_rejects(case):
    assert probe._report_allowed(tampered()[case]) is False


def test_report_allowlist_accepts():
    assert probe._report_allowed(vrep()) is True
