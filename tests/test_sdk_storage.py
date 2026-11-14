import asyncio
import importlib
import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from ezviz_unified import (
    ApiError,
    AsyncClient,
    AuthenticationError,
    Client,
    ProtocolError,
    RegionConfig,
    TransportError,
)

START = datetime(2026, 1, 2, 3, 4, 5)
END = START + timedelta(hours=1)
READS = {"spaces", "search_files", "download_urls"}
CASES = {
    "create_space": (
        "POST",
        "/api/service/open/storage/engine/space",
        "header",
        {"spaceName": "录像_1", "bizType": "record"},
        42,
    ),
    "update_space": (
        "PUT",
        "/api/service/open/storage/engine/space",
        "header",
        {"spaceId": "42", "spaceName": "录像_2"},
        True,
    ),
    "delete_space": (
        "DELETE",
        "/api/service/open/storage/engine/space",
        "header",
        {"spaceId": "42"},
        None,
    ),
    "spaces": (
        "GET",
        "/api/service/open/storage/engine/space/listById",
        "header",
        {"startTime": "2026-01-02 03:04:05", "endTime": "2026-01-02 04:04:05"},
        {"result": [], "lastId": 42, "hasNext": True},
    ),
    "set_file_retention": (
        "POST",
        "/api/service/open/storage/engine/file/batchExpire",
        "header",
        {"fileNodeIds": "PRIVATE_NODE_A,PRIVATE_NODE_B", "expireDays": "-1"},
        None,
    ),
    "search_files": (
        "POST",
        "/api/service/open/storage/engine/file/listById",
        "header",
        {"fileNodeIds": "PRIVATE_NODE_A,PRIVATE_NODE_B", "fileChildType": "06"},
        {
            "result": [{"folderNode": "PRIVATE_NODE_A", "fileChildType": "06"}],
            "lastId": "",
            "hasNext": False,
        },
    ),
    "download_urls": (
        "GET",
        "/api/service/open/storage/engine/file/downloadurls",
        "query",
        {"fileNodeIds": "PRIVATE_NODE_A,PRIVATE_NODE_B"},
        {
            "urls": {"PRIVATE_NODE_A": "https://files.example.invalid/test?sig=SYNTHETIC"},
            "expireTime": 2000123,
        },
    ),
}


def file_query(**kwargs):
    assert importlib.util.find_spec("ezviz_unified.storage_models"), "Storage models missing"
    return importlib.import_module("ezviz_unified.storage_models").StorageFileQuery(**kwargs)


def arguments(name):
    if name == "create_space":
        return ("录像_1",), {"biz_type": "record"}
    if name == "update_space":
        return (42,), {"name": "录像_2"}
    if name == "delete_space":
        return (42,), {}
    if name == "spaces":
        return (), {"start_time": START, "end_time": END}
    if name == "set_file_retention":
        return (["PRIVATE_NODE_A", "PRIVATE_NODE_B"], -1), {}
    if name == "search_files":
        return (
            file_query(file_node_ids=["PRIVATE_NODE_A", "PRIVATE_NODE_B"], file_child_type="06"),
        ), {}
    return (["PRIVATE_NODE_A", "PRIVATE_NODE_B"],), {}


def execute(
    kind,
    name,
    *,
    args=None,
    kwargs=None,
    region=None,
    response=None,
    http_status=200,
    auth_fault=False,
    network_fault=False,
    requests=None,
    authentication=None,
):
    default_args, default_kwargs = arguments(name)
    args = default_args if args is None else args
    kwargs = default_kwargs if kwargs is None else kwargs
    requests = [] if requests is None else requests
    authentication = [] if authentication is None else authentication

    def auth(req):
        authentication.append(req)
        return httpx.Response(
            200,
            json={
                "code": "200",
                "data": {"accessToken": f"at.EVAL_{len(authentication)}", "expireTime": 2000000},
            },
        )

    def business(req):
        requests.append(req)
        if network_fault:
            raise httpx.ReadTimeout("unavailable", request=req)
        body = (
            response
            if response is not None
            else {"meta": {"code": 200, "message": "ok"}, "data": CASES[name][4]}
        )
        if auth_fault and len(requests) == 1:
            body = {"meta": {"code": 10002, "message": "expired"}}
        return httpx.Response(http_status, json=body)

    options = dict(
        region=region or RegionConfig.china(),
        clock=lambda: 1000,
        auth_transport=httpx.MockTransport(auth),
        transport_factory=lambda origin: httpx.MockTransport(business),
    )
    try:
        if kind == "sync":
            with Client("EVAL_KEY", "EVAL_SECRET", **options) as client:
                assert hasattr(client, "storage"), "Storage facade missing"
                result = getattr(client.storage, name)(*args, **kwargs)
        else:

            async def run():
                async with AsyncClient("EVAL_KEY", "EVAL_SECRET", **options) as client:
                    assert hasattr(client, "storage"), "Storage facade missing"
                    return await getattr(client.storage, name)(*args, **kwargs)

            result = asyncio.run(run())
    except ValueError:
        assert not requests and not authentication
        raise
    return result, requests, authentication


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name", CASES)
def test_storage_exact_transport_and_raw_response(kind, name):
    result, requests, auth = execute(kind, name)
    assert len(requests) == len(auth) == 1
    req = requests[0]
    method, path, token_location, expected, data = CASES[name]
    assert req.method == method and req.url.path == path and req.url.host == "open.ys7.com"
    fields = (
        dict(req.url.params)
        if method == "GET"
        else {k: v[0] for k, v in parse_qs(req.content.decode(), keep_blank_values=True).items()}
    )
    if token_location == "query":
        assert fields.pop("accessToken") == "at.EVAL_1" and "accessToken" not in req.headers
    else:
        assert req.headers["accessToken"] == "at.EVAL_1"
    assert fields == expected
    if method == "GET":
        assert not req.content
    else:
        assert not req.url.query
        assert req.headers["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert type(result.data) is type(data) and result.data == data
    assert result.raw == {"meta": {"code": 200, "message": "ok"}, "data": data}


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name", CASES)
def test_storage_does_not_infer_overseas_support(kind, name):
    with pytest.raises(ValueError):
        execute(kind, name, region=RegionConfig.overseas())


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name", CASES)
def test_only_queries_recover_from_token_expiry(kind, name):
    requests = []
    auth = []
    if name in READS:
        execute(kind, name, auth_fault=True, requests=requests, authentication=auth)
        assert len(requests) == len(auth) == 2
    else:
        with pytest.raises(AuthenticationError):
            execute(kind, name, auth_fault=True, requests=requests, authentication=auth)
        assert len(requests) == len(auth) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name", CASES)
def test_http_failure_with_success_body_is_not_replayed(kind, name):
    requests = []
    with pytest.raises(ApiError) as captured:
        execute(kind, name, http_status=500, requests=requests)
    assert captured.value.http_status == 500 and len(requests) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize(
    "code,error_type", [("200", ApiError), (200.0, ProtocolError), (True, ProtocolError)]
)
def test_storage_success_code_type_is_strict(kind, code, error_type):
    with pytest.raises(error_type):
        execute(kind, "create_space", response={"meta": {"code": code}})


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_unknown_write_outcome_is_not_replayed(kind):
    requests = []
    with pytest.raises(TransportError):
        execute(kind, "set_file_retention", network_fault=True, requests=requests)
    assert len(requests) == 1


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_none_update_fields_keep_server_values_and_false_data_is_preserved(kind):
    body = {"meta": {"code": 200}, "data": False}
    result, requests, _ = execute(
        kind, "update_space", kwargs={"permission": 2, "flow_limit": 0}, response=body
    )
    assert parse_qs(requests[0].content.decode()) == {
        "spaceId": ["42"],
        "permission": ["2"],
        "flowLimit": ["0"],
    }
    assert result.data is False and result.raw == body
    result, _, _ = execute(kind, "delete_space", response={"meta": {"code": 200}})
    assert result.data is None


INVALID = [
    ("create_space", (None,), {"biz_type": "record"}),
    ("create_space", ("camera-1",), {"biz_type": "record"}),
    ("create_space", ("α",), {"biz_type": "record"}),
    ("create_space", ("🙂",), {"biz_type": "record"}),
    ("create_space", ("汉" * 33,), {"biz_type": "record"}),
    ("create_space", ("Valid",), {"biz_type": "cloudcapture"}),
    ("create_space", ("Valid",), {"biz_type": "record", "expire_days": 0}),
    ("create_space", ("Valid",), {"biz_type": "record", "expire_days": 5476}),
    ("create_space", ("Valid",), {"biz_type": "record", "expire_days": True}),
    ("create_space", ("Valid",), {"biz_type": "record", "storage_type": 0}),
    ("create_space", ("Valid",), {"biz_type": "record", "permission": 3}),
    ("create_space", ("Valid",), {"biz_type": "record", "flow_limit": False}),
    ("update_space", (42,), {}),
    ("update_space", (False,), {"name": "Valid"}),
    ("delete_space", (True,), {}),
    ("spaces", (), {"start_time": START, "end_time": START + timedelta(days=30, seconds=1)}),
    ("spaces", (), {"start_time": START, "end_time": END, "biz_type": "vod,record"}),
    ("spaces", (), {"start_time": START, "end_time": END, "sort_rule": "ASC"}),
    ("spaces", (), {"start_time": START, "end_time": END, "sort_field": "id"}),
    ("spaces", (), {"start_time": START, "end_time": END, "page_size": 51}),
    ("spaces", (), {"start_time": START, "end_time": END, "last_space_id": True}),
    ("spaces", (), {"start_time": START.replace(tzinfo=timezone.utc), "end_time": END}),
    ("spaces", (), {"start_time": START.replace(microsecond=1), "end_time": END}),
    ("download_urls", ([],), {}),
    ("download_urls", (["N"] * 51,), {}),
    ("download_urls", (["N"],), {"expire_seconds": 29}),
    ("download_urls", (["N"],), {"expire_seconds": 604801}),
    ("download_urls", (["N"],), {"expire_seconds": True}),
    ("set_file_retention", (["N"], 0), {}),
    ("set_file_retention", ("N", 1), {}),
]


@pytest.mark.parametrize("kind", ["sync", "async"])
@pytest.mark.parametrize("name,args,kwargs", INVALID)
def test_invalid_inputs_are_rejected_before_authentication(kind, name, args, kwargs):
    with pytest.raises(ValueError):
        execute(kind, name, args=args, kwargs=kwargs)


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_documented_inclusive_bounds_and_no_extra_batch_limit(kind):
    execute(
        kind,
        "create_space",
        args=("汉" * 32,),
        kwargs={
            "biz_type": "record",
            "expire_days": 5475,
            "storage_type": 2,
            "permission": 2,
            "flow_limit": -1,
        },
    )
    execute(
        kind,
        "spaces",
        kwargs={
            "start_time": datetime(1, 1, 1),
            "end_time": datetime(1, 1, 31),
            "page_size": 50,
            "sort_rule": "asc",
            "sort_field": "updateTime",
            "biz_type": "vod",
        },
    )
    _, requests, _ = execute(kind, "set_file_retention", args=([str(x) for x in range(51)], 1))
    assert parse_qs(requests[0].content.decode())["fileNodeIds"][0].endswith(",50")
    for bound in (30, 604800):
        execute(kind, "download_urls", kwargs={"expire_seconds": bound})


@pytest.mark.parametrize("kind", ["sync", "async"])
def test_resource_ids_and_names_are_redacted_without_changing_wire(kind, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    body = {"meta": {"code": 400, "message": "PRIVATE_NODE_A missing; PRIVATE_NODE_B restricted"}}
    requests = []
    with pytest.raises(ApiError) as captured:
        execute(kind, "download_urls", response=body, requests=requests)
    assert requests[0].url.params["fileNodeIds"] == "PRIVATE_NODE_A,PRIVATE_NODE_B"
    for secret in ("PRIVATE_NODE_A", "PRIVATE_NODE_B", "at.EVAL_1"):
        assert secret not in str(captured.value) and secret not in caplog.text
    with pytest.raises(ApiError) as named:
        execute(
            kind,
            "create_space",
            args=("PRIVATE_SPACE",),
            response={"meta": {"code": 400, "message": "PRIVATE_SPACE exists"}},
        )
    assert "PRIVATE_SPACE" not in str(named.value)
