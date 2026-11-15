import copy
import importlib
from pathlib import Path

import pytest


def module():
    assert Path("harness/registry.py").exists(), "Registry auditor missing"
    return importlib.import_module("harness.registry")


BASE_FIXTURES = [
    {
        "schema_version": 1,
        "contracts": [
            {
                "operation_id": "cn.feature.get",
                "region": "domestic",
                "source_doc_id": "1",
                "source_url": "https://open.ys7.com/help/1",
                "source_content_sha256": "a" * 64,
                "method": "POST",
                "path": "/api/feature/get",
                "read_only": True,
                "body_encoding": "form_urlencoded",
                "token_location": "form",
                "parameters": [
                    {
                        "name": "accessToken",
                        "location": "body",
                        "type": "String",
                        "required": True,
                    },
                    {
                        "name": "deviceSerial",
                        "location": "body",
                        "type": "String",
                        "required": True,
                    },
                ],
                "success": {"code_path": "meta.code", "values": [200]},
            }
        ],
    },
    {
        "schema_version": 1,
        "bindings": [
            {
                "operation_id": "cn.feature.get",
                "region": "domestic",
                "source_doc_id": "1",
                "module": "src/ezviz_unified/feature.py",
                "symbols": ["Feature.get", "AsyncFeature.get"],
                "offline_status": "passed",
                "live_status": "not_verified",
            }
        ],
    },
    {
        "schema_version": 1,
        "issues": [
            {
                "id": "source_conflict",
                "region": "domestic",
                "source_doc_id": "1",
                "source_url": "https://open.ys7.com/help/1",
                "source_content_sha256": "a" * 64,
                "description": "source conflict",
                "status": "unresolved_source_semantics",
                "sdk_handling": "preserve raw",
            }
        ],
    },
]


def fixtures():
    return copy.deepcopy(BASE_FIXTURES)


def business_param(data):
    return data[0]["contracts"][0]["parameters"][1]


def test_baseline_structure_still_valid():
    result = module().audit_registry(*fixtures())
    assert result["summary"]["contracts"] == 1


def test_business_parameter_required_null_with_reason_is_accepted():
    data = fixtures()
    business_param(data).update(
        required=None,
        required_uncertainty="source document omits requirement flag",
    )
    result = module().audit_registry(*data)
    assert result["summary"]["contracts"] == 1


def test_business_parameter_required_false_still_accepted():
    data = fixtures()
    business_param(data)["required"] = False
    module().audit_registry(*data)


@pytest.mark.parametrize(
    "update",
    [
        {"required": None},
        {"required": None, "required_uncertainty": ""},
        {"required": None, "required_uncertainty": "   "},
        {"required": None, "required_uncertainty": 7},
        {"required": "PRIVATE_REQUIRED_VALUE"},
        {"required": 1},
        {"required": 0},
    ],
    ids=[
        "null_without_reason",
        "null_empty_reason",
        "null_blank_reason",
        "null_non_string_reason",
        "string_value",
        "int_one",
        "int_zero",
    ],
)
def test_business_parameter_invalid_required_rejected(update):
    data = fixtures()
    business_param(data).update(update)
    with pytest.raises(module().RegistryError) as caught:
        module().audit_registry(*data)
    assert "PRIVATE_REQUIRED_VALUE" not in str(caught.value)


@pytest.mark.parametrize("with_reason", [True, False], ids=["with_reason", "without_reason"])
def test_business_parameter_missing_required_key_rejected(with_reason):
    data = fixtures()
    del business_param(data)["required"]
    if with_reason:
        business_param(data)["required_uncertainty"] = "PRIVATE_MISSING_REASON"
    with pytest.raises(module().RegistryError) as caught:
        module().audit_registry(*data)
    assert "PRIVATE_MISSING_REASON" not in str(caught.value)


def test_access_token_required_null_rejected_even_with_reason():
    data = fixtures()
    token = data[0]["contracts"][0]["parameters"][0]
    token.update(
        required=None,
        required_uncertainty="PRIVATE_TOKEN_REASON",
    )
    with pytest.raises(module().RegistryError) as caught:
        module().audit_registry(*data)
    assert "PRIVATE_TOKEN_REASON" not in str(caught.value)
