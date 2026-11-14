import json

import httpx
import pytest

from ezviz_unified import ApiError, ApiResponse
from tools.live import verify_devices


@pytest.mark.parametrize(
    "online,disk,expected_storage_calls", [(1, "1", 2), (0, "1", 0), (1, "0", 0)]
)
def test_optional_diagnostics_are_bounded_and_report_only_safe_fields(
    monkeypatch, online, disk, expected_storage_calls
):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.devices = self.diagnostics = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list(self, *, page_size):
            return ApiResponse(200, "200", data=[{"deviceSerial": "PRIVATE_SERIAL"}])

        def get(self, serial):
            return ApiResponse(200, "200", data={"deviceName": "PRIVATE_NAME", "status": online})

        def channels(self, serial):
            return ApiResponse(200, "200", data=[])

        def status(self, serial):
            return ApiResponse(200, "200", data={})

        def capabilities(self, serial):
            calls.append("capabilities")
            return ApiResponse(
                200,
                "200",
                data={
                    "support_disk": disk,
                    "support_capture": "1",
                    "private_field": "PRIVATE_VALUE",
                },
            )

        def version(self, serial):
            calls.append("version")
            return ApiResponse(
                200,
                "200",
                data={
                    "currentVersion": "PRIVATE_VERSION",
                    "latestVersion": "PRIVATE_LATEST",
                    "isNeedUpgrade": 1,
                    "isUpgrading": 0,
                },
            )

        def storage_status(self, serial):
            calls.append("storage_status")
            return ApiResponse(
                200,
                200,
                data={"storageStatus": [{"index": "1", "name": "PRIVATE_NAME", "status": "0"}]},
            )

        def disk_capacity(self, serial):
            calls.append("disk_capacity")
            return ApiResponse(200, "200", data={"diskCapacity": "32,-1"})

    monkeypatch.setattr(verify_devices, "Client", Client)
    result = verify_devices.verify(
        {"region": "domestic", "app_key": "K", "app_secret": "S", "include_diagnostics": "true"}
    )
    assert calls[:2] == ["capabilities", "version"]
    assert len(calls) == 2 + expected_storage_calls
    assert "PRIVATE" not in json.dumps(result)
    assert next(x for x in result["results"] if x["operation"] == "devices.get")["online"] is (
        online == 1
    )
    assert next(x for x in result["results"] if x["operation"] == "diagnostics.capabilities")[
        "advertised_flags"
    ]["support_disk"] is (disk == "1")


def test_probe_transport_rejects_writes_before_delegating():
    assert hasattr(verify_devices, "_ReadOnlyTransport"), "Read-only probe guard missing"
    seen = []
    delegate = httpx.MockTransport(lambda request: seen.append(request) or httpx.Response(200))
    with httpx.Client(transport=verify_devices._ReadOnlyTransport(delegate)) as client:
        with pytest.raises(ValueError):
            client.post("https://open.ys7.com/api/lapp/device/encrypt/off")
        with pytest.raises(ValueError):
            client.put("https://open.ys7.com/api/v3/device/format/disk")
        with pytest.raises(ValueError):
            client.get("https://different.example.invalid/api/v3/device/format/status")
        client.get("https://open.ys7.com/api/v3/device/format/status")
    assert len(seen) == 1


def test_public_receipt_omits_private_device_state_and_capabilities():
    assert hasattr(verify_devices, "_public_receipt"), "Public receipt projection missing"
    report = {
        "recorded_at_utc": "2026-11-14T00:00:00Z",
        "region": "domestic",
        "read_only": True,
        "auth_origin": "https://open.ys7.com",
        "business_origins": ["https://open.ys7.com"],
        "results": [
            {"operation": "devices.get", "status": "passed", "online": True},
            {
                "operation": "diagnostics.capabilities",
                "status": "passed",
                "advertised_flags": {"support_disk": False},
            },
            {
                "operation": "diagnostics.storage_status",
                "status": "not_verified",
                "reason": "storage_capability_not_advertised",
            },
            {
                "operation": "diagnostics.version",
                "status": "passed",
                "upgrade_needed_reported": True,
            },
        ],
    }
    public = verify_devices._public_receipt(report)
    text = json.dumps(public)
    assert "online" not in text and "advertised_flags" not in text and "upgrade_needed" not in text
    assert public["results"][2]["reason"] == "not_selected_for_this_probe"
    assert report["results"][0]["online"] is True


def test_live_verifier_calls_only_bounded_read_operations_and_redacts_report(monkeypatch):
    calls = []

    class Devices:
        def list(self, *, page_size):
            calls.append(("list", page_size))
            return ApiResponse(200, "200", data=[{"deviceSerial": "PRIVATE_DEVICE"}])

        def get(self, serial):
            calls.append(("get", serial))
            return ApiResponse(200, "200", data={"deviceName": "PRIVATE_NAME"})

        def channels(self, serial):
            calls.append(("channels", serial))
            return ApiResponse(200, "200", data=[])

        def status(self, serial):
            calls.append(("status", serial))
            raise ApiError("PRIVATE_MESSAGE", code="PRIVATE_CODE", http_status=400)

    class Client:
        def __init__(self, *args, **kwargs):
            self.devices = Devices()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(verify_devices, "Client", Client)
    result = verify_devices.verify(
        {"region": "domestic", "app_key": "PRIVATE_KEY", "app_secret": "PRIVATE_SECRET"}
    )
    assert calls == [
        ("list", 1),
        ("get", "PRIVATE_DEVICE"),
        ("channels", "PRIVATE_DEVICE"),
        ("status", "PRIVATE_DEVICE"),
    ]
    assert "PRIVATE" not in json.dumps(result)
    assert result["results"][-1] == {
        "operation": "devices.status",
        "status": "failed",
        "error_type": "ApiError",
        "code": None,
        "http_status": 400,
    }


def test_empty_account_is_not_reported_as_device_validation(monkeypatch):
    class Client:
        def __init__(self, *args, **kwargs):
            self.devices = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list(self, *, page_size):
            return ApiResponse(200, "200", data=[])

    monkeypatch.setattr(verify_devices, "Client", Client)
    result = verify_devices.verify({"region": "overseas", "app_key": "K", "app_secret": "S"})
    assert result["results"][-1]["status"] == "not_verified"
    assert result["results"][-1]["reason"] == "no_device"


def test_managed_inventory_is_opt_in_and_does_not_trigger_detail_queries(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.devices = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list(self, *, page_size):
            calls.append("ordinary")
            return ApiResponse(200, "200", data=[])

        def list_managed(self, *, page_size):
            assert page_size == 1
            calls.append("managed")
            return ApiResponse(200, "200", data=[{"deviceSerial": "PRIVATE_MANAGED_DEVICE"}])

    monkeypatch.setattr(verify_devices, "Client", Client)
    result = verify_devices.verify(
        {"region": "domestic", "app_key": "K", "app_secret": "S", "include_managed": "true"}
    )
    assert calls == ["ordinary", "managed"]
    assert {"operation": "devices.list_managed", "status": "passed", "returned_items": 1} in result[
        "results"
    ]
    assert "PRIVATE" not in json.dumps(result)


def test_protocol_probe_uses_public_sample_and_does_not_query_devices(monkeypatch):
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            self.diagnostics = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def protocol_support(self, model, version, *, app_key):
            calls.append((model, version, app_key))
            return ApiResponse(200, "200", data=[{"isSupport": 1}])

    monkeypatch.setattr(verify_devices, "Client", Client)
    result = verify_devices.verify(
        {
            "region": "domestic",
            "app_key": "PRIVATE_KEY",
            "app_secret": "PRIVATE_SECRET",
            "protocol_only": "true",
        }
    )
    assert calls == [("CS-C1-10F", "V4.1.0 build 130101", "PRIVATE_KEY")]
    assert result["results"][0]["operation"] == "diagnostics.protocol_support"
    assert result["results"][0]["reported_support"] == [1] and "PRIVATE" not in json.dumps(result)
