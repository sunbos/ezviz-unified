"""Boundary regressions for third-party protocol registration helpers."""

from collections.abc import Sequence

import httpx
import pytest
from test_sdk_third_party_protocols import Probe

from ezviz_unified import AuthenticationError, PermissionDeniedError

MODES = ("sync", "async")
FAILURE_CODES = (
    ("10002", AuthenticationError, "invalid_token"),
    ("10031", PermissionDeniedError, "permission_denied"),
)


class Oversized(Sequence):
    """Reports length 301; any element access raises, proving the limit
    check runs on __len__ before the sequence is ever iterated."""

    def __len__(self):
        return 301

    def __getitem__(self, index):
        raise AssertionError("oversized sequence must not be iterated")


@pytest.mark.parametrize("mode", MODES)
def test_apply_batch_rejects_bad_device_code_before_transport(mode):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, "apply_batch", "dhreg", device_codes=["A", "bad" + chr(0xD800)])
    assert not probe.auth and not probe.biz


@pytest.mark.parametrize("mode", MODES)
def test_apply_batch_rejects_oversized_sequence_before_iteration(mode):
    probe = Probe()
    with pytest.raises(ValueError):
        probe.call(mode, "apply_batch", "dhreg", device_codes=Oversized())
    with pytest.raises(ValueError):
        probe.call(
            mode,
            "apply_batch",
            "jt808",
            device_codes=["A"],
            vehicle_plates=["测A12345"],
            vehicle_colors=Oversized(),
        )
    assert not probe.auth and not probe.biz


@pytest.mark.parametrize("mode", MODES)
def test_apply_batch_ignores_count_when_codes_supplied(mode):
    probe = Probe({"meta": {"code": 200, "message": "ok"}, "data": {"taskId": 7}})
    probe.call(mode, "apply_batch", "dhreg", device_codes=["A", "B"], device_count=0)
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    assert httpx.QueryParams(probe.biz[0].content.decode("utf-8")).multi_items() == [
        ("deviceCode", "A,B")
    ]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("raw_code,error_type,category", FAILURE_CODES)
def test_open_project_failure_redacts_code_and_private_data(mode, raw_code, error_type, category):
    probe = Probe(
        {"meta": {"code": raw_code, "message": "synthetic"}, "data": {"deviceSerial": "1"}}
    )
    with pytest.raises(error_type) as excinfo:
        probe.call(mode, "open_project", "dhreg")
    assert excinfo.value.code != raw_code
    assert raw_code not in str(excinfo.value.code)
    if error_type is AuthenticationError:
        assert excinfo.value.invalid_token is True
    assert len(probe.auth) == 1 and len(probe.biz) == 1
    attempts = [
        e
        for e in probe.recorder.report()["events"]
        if e.get("kind") == "attempt"
        and e.get("entrypoint") == "client.request"
        and e.get("operation") == "third_party_protocols.open_project"
        and e.get("mode") == mode
    ]
    assert attempts
    terminal = attempts[-1]
    assert terminal["outcome"] == "failed"
    assert terminal["code_category"] == category
    assert raw_code not in terminal.values()
    assert "deviceSerial" not in terminal
    assert "1" not in terminal.values()
