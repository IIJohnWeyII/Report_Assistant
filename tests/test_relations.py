from copy import deepcopy

from fastapi.testclient import TestClient
import pymysql
import pytest

from app.main import app

client = TestClient(app)
OUTPUT = "DEMO_OUTPUT_202610"
COST = "DEMO_COST_202610"
MATERIAL = "DEMO_MATERIAL_202610"
LABOR = "DEMO_LABOR_202610"


def pair(left=OUTPUT, right=COST, join_type="INNER"):
    return {"left_task_no": left, "right_task_no": right, "join_type": join_type,
            "links": [{"left_field_code": "project_code", "right_field_code": "project_code"},
                      {"left_field_code": "period", "right_field_code": "period"}]}


def test_field_metadata_uses_all_record_templates_and_preserves_names():
    response = client.post("/api/fields", json={"report_task_nos": [COST, OUTPUT]})
    assert response.status_code == 200
    tasks = response.json()["tasks"]
    assert [task["report_task_no"] for task in tasks] == [COST, OUTPUT]
    fields = {field["field_code"]: field for field in tasks[1]["fields"]}
    assert {definition["field_name"] for definition in fields["project_code"]["definitions"]} == {"项目编码", "工程编码"}
    assert {definition["report_template_no"] for definition in fields["project_code"]["definitions"]} == {"TPL_OUTPUT", "TPL_OUTPUT_OLD"}
    assert fields["project_name"]["definitions"][0]["field_name"] == fields["legacy_project_name"]["definitions"][0]["field_name"]
    assert "legacy_zone" in fields
    assert "current_only" not in fields
    assert "deleted_record_only" not in fields
    assert "deleted_field" not in fields
    assert fields["completion_rate"]["definitions"][0]["field_kind"] == "DERIVED"
    assert "row_data" not in response.text


@pytest.mark.parametrize("task_no", ["missing", "DEMO_EXCLUDED_17", "DEMO_EXCLUDED_18", "DEMO_EXCLUDED_19", "' OR 1=1 --"])
def test_field_lookup_rejects_unknown_or_ineligible_tasks(task_no):
    assert client.post("/api/fields", json={"report_task_nos": [task_no]}).status_code == 404


def test_no_records_returns_empty_fields_without_using_current_template():
    # Seeded tasks normally have records; simulate an empty metadata result using a real transaction,
    # then roll back so no fixture changes escape this test.
    from app.relations import get_task_fields
    from scripts.local_db import admin, DATABASE

    with admin() as connection:
        connection.select_db(DATABASE)
        connection.begin()
        try:
            with connection.cursor() as cursor:
                cursor.execute("UPDATE fi_fill_record SET delete_flag = 1 WHERE report_task_no = %s", (LABOR,))
            connection.cursorclass = pymysql.cursors.DictCursor
            tasks = get_task_fields(connection, [LABOR])
            assert tasks[0]["fields"] == []
        finally:
            connection.rollback()


@pytest.mark.parametrize("join_type", ["INNER", "LEFT"])
def test_multiple_conditions_round_trip_with_explicit_join_type(join_type):
    config = {"report_task_nos": [OUTPUT, COST], "pairs": [pair(join_type=join_type)]}
    response = client.post("/api/relations/validate", json=config)
    assert response.status_code == 200
    assert response.json() == config


def test_disconnected_groups_unlinked_tasks_and_cycles_are_permitted():
    for groups in [[], [pair()], [pair(), pair(MATERIAL, LABOR)],
                   [pair(), pair(COST, MATERIAL), pair(MATERIAL, OUTPUT)]]:
        config = {"report_task_nos": [OUTPUT, COST, MATERIAL, LABOR], "pairs": groups}
        response = client.post("/api/relations/validate", json=config)
        assert response.status_code == 200
        assert response.json() == config


def test_single_task_requires_no_pair():
    config = {"report_task_nos": [OUTPUT], "pairs": []}
    assert client.post("/api/relations/validate", json=config).json() == config


def test_arbitrary_field_types_are_allowed_for_string_alignment():
    group = pair()
    group["links"] = [{"left_field_code": "actual_output", "right_field_code": "project_code"}]
    response = client.post("/api/relations/validate", json={"report_task_nos": [OUTPUT, COST], "pairs": [group]})
    assert response.status_code == 200


def test_left_join_direction_is_not_normalized_or_sorted():
    group = pair(COST, OUTPUT, "LEFT")
    group["links"] = [{"left_field_code": "total_cost", "right_field_code": "actual_output"}]
    config = {"report_task_nos": [OUTPUT, COST], "pairs": [group]}
    response = client.post("/api/relations/validate", json=config)
    assert response.json() == config


@pytest.mark.parametrize("change", ["same_task", "unselected_task", "unknown_field", "wrong_side_field",
                                   "empty_links", "duplicate_link", "duplicate_pair", "reverse_duplicate_pair",
                                   "unknown_join", "extra_field", "missing_join", "duplicate_task", "empty_tasks"])
def test_invalid_configs_are_rejected(change):
    config = {"report_task_nos": [OUTPUT, COST], "pairs": [pair()]}
    group = config["pairs"][0]
    if change == "same_task":
        group["right_task_no"] = OUTPUT
    elif change == "unselected_task":
        group["right_task_no"] = MATERIAL
    elif change == "unknown_field":
        group["links"][0]["left_field_code"] = "current_only"
    elif change == "wrong_side_field":
        group["links"][0]["left_field_code"] = "total_cost"
    elif change == "empty_links":
        group["links"] = []
    elif change == "duplicate_link":
        group["links"].append(deepcopy(group["links"][0]))
    elif change == "duplicate_pair":
        config["pairs"].append(deepcopy(group))
    elif change == "reverse_duplicate_pair":
        config["pairs"].append(pair(COST, OUTPUT))
    elif change == "unknown_join":
        group["join_type"] = "CROSS"
    elif change == "extra_field":
        group["sql"] = "SELECT anything"
    elif change == "missing_join":
        del group["join_type"]
    elif change == "duplicate_task":
        config["report_task_nos"].append(OUTPUT)
    elif change == "empty_tasks":
        config["report_task_nos"] = []
    assert client.post("/api/relations/validate", json=config).status_code == 422


def test_relation_validation_rechecks_task_eligibility():
    config = {"report_task_nos": [OUTPUT, "DEMO_EXCLUDED_19"], "pairs": []}
    assert client.post("/api/relations/validate", json=config).status_code == 404
