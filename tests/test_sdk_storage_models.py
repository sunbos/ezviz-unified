import importlib
import importlib.util
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

START = datetime(2026, 1, 2, 3, 4, 5)
END = START + timedelta(hours=1)


def module():
    assert importlib.util.find_spec("ezviz_unified.storage_models"), "Storage models missing"
    return importlib.import_module("ezviz_unified.storage_models")


def query(**kwargs):
    return module().StorageFileQuery(**kwargs)


def test_query_snapshots_identifiers_and_returns_fresh_form():
    ids = ["PRIVATE_NODE_A", "PRIVATE_NODE_B", "PRIVATE_NODE_A"]
    spaces = [42]
    q = query(file_node_ids=ids, space_ids=spaces, tag_id="PRIVATE_TAG")
    ids.append("CHANGED")
    spaces.append(100)
    form = q.to_form()
    assert form == {
        "fileNodeIds": "PRIVATE_NODE_A,PRIVATE_NODE_B,PRIVATE_NODE_A",
        "spaceIds": "42",
        "tagId": "PRIVATE_TAG",
    }
    form["tagId"] = "CHANGED"
    assert q.to_form()["tagId"] == "PRIVATE_TAG"
    assert "PRIVATE_NODE" not in repr(q) and "PRIVATE_TAG" not in repr(q)
    with pytest.raises(FrozenInstanceError):
        q.tag_id = "CHANGED"


def test_selector_precedence_is_preserved_without_silent_dropping():
    q = query(
        file_node_ids=["NODE"],
        tag_id="TAG",
        space_ids=[1, 2],
        start_time=START,
        end_time=END,
        file_type=0,
        file_child_type="06",
        page_size=50,
        last_id="",
        sort_rule=0,
        source_type=-1,
    )
    assert q.to_form() == {
        "fileNodeIds": "NODE",
        "tagId": "TAG",
        "spaceIds": "1,2",
        "startTime": "2026-01-02 03:04:05",
        "endTime": "2026-01-02 04:04:05",
        "fileType": "0",
        "fileChildType": "06",
        "pageSize": 50,
        "lastId": "",
        "sortRule": 0,
        "sourceType": -1,
    }


def test_search_has_no_invented_thirty_day_or_fifty_node_limit():
    assert query(file_node_ids=[str(i) for i in range(51)]).to_form()["fileNodeIds"].endswith(",50")
    q = query(space_ids=[1, 2, 3, 4, 5], start_time=START, end_time=START + timedelta(days=31))
    assert q.to_form()["endTime"] == "2026-02-02 03:04:05"
    assert query(tag_id="TAG", start_time=START).to_form() == {
        "tagId": "TAG",
        "startTime": "2026-01-02 03:04:05",
    }
    assert query(tag_id="TAG", end_time=END).to_form() == {
        "tagId": "TAG",
        "endTime": "2026-01-02 04:04:05",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"file_node_ids": []},
        {"file_node_ids": "NODE"},
        {"file_node_ids": ["A,B"]},
        {"file_node_ids": [" "]},
        {"file_node_ids": [3]},
        {"space_ids": []},
        {"space_ids": [1]},
        {"space_ids": [1], "start_time": START},
        {"space_ids": [True], "start_time": START, "end_time": END},
        {"space_ids": [1, 2, 3, 4, 5, 6], "start_time": START, "end_time": END},
        {"tag_id": " "},
        {"tag_id": "TAG", "file_type": True},
        {"tag_id": "TAG", "file_type": 5},
        {"tag_id": "TAG", "file_child_type": " "},
        {"tag_id": "TAG", "page_size": 0},
        {"tag_id": "TAG", "page_size": 51},
        {"tag_id": "TAG", "page_size": True},
        {"tag_id": "TAG", "sort_rule": True},
        {"tag_id": "TAG", "sort_rule": 2},
        {"tag_id": "TAG", "source_type": False},
        {"tag_id": "TAG", "last_id": " "},
        {"tag_id": "TAG", "start_time": START.replace(tzinfo=timezone.utc)},
        {"tag_id": "TAG", "start_time": START.replace(microsecond=1)},
        {"tag_id": "TAG", "start_time": END, "end_time": START},
    ],
)
def test_invalid_query_fields_fail_without_echoing_input(kwargs):
    with pytest.raises(ValueError) as captured:
        query(**kwargs)
    assert "TAG" not in str(captured.value)


def test_retired_search_alias_is_not_accepted():
    with pytest.raises(TypeError):
        query(folder_nodes=["NODE"])
