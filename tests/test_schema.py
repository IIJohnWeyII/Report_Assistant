from copy import deepcopy

from fastapi.testclient import TestClient
import pymysql
import pytest

from app import db
from app.functions import build_wide_table_schema
from app.main import app
from app.relations import RelationConfig
from scripts.local_db import admin, DATABASE

client = TestClient(app)
OUTPUT, COST, MATERIAL, LABOR = [f"DEMO_{name}_202610" for name in ("OUTPUT", "COST", "MATERIAL", "LABOR")]


def config():
    return {"report_task_nos": [OUTPUT, COST, MATERIAL], "pairs": [
        {"left_task_no": OUTPUT, "right_task_no": COST, "join_type": "LEFT", "links": [
            {"left_field_code": "project_code", "right_field_code": "project_code"},
            {"left_field_code": "period", "right_field_code": "period"}]},
        {"left_task_no": OUTPUT, "right_task_no": MATERIAL, "join_type": "INNER", "links": [
            {"left_field_code": "project_code", "right_field_code": "project_code"}]}]}


@pytest.fixture
def metadata_transaction():
    with admin() as connection:
        connection.select_db(DATABASE)
        connection.cursorclass = pymysql.cursors.DictCursor
        connection.begin()
        try:
            yield connection
        finally:
            connection.rollback()


def test_schema_columns_and_join_keys_are_task_qualified():
    response = client.post("/api/schema", json=config())
    assert response.status_code == 200
    result = response.json()
    assert result["pairs"] == config()["pairs"]
    assert result["report_task_nos"] == [OUTPUT, COST, MATERIAL]
    columns = result["columns"]
    assert len(columns) == len({column["column_id"] for column in columns}) == 22
    keys = {column["column_id"] for column in columns if column["is_join_key"]}
    assert keys == {f"{OUTPUT}.project_code", f"{OUTPUT}.period", f"{COST}.project_code",
                    f"{COST}.period", f"{MATERIAL}.project_code"}
    actual = next(column for column in columns if column["column_id"] == f"{OUTPUT}.actual_output")
    assert actual["data_type"] == "NUMBER"
    assert actual["data_types"] == ["NUMBER"]
    assert actual["is_join_key"] is False


def test_historical_aliases_and_duplicate_names_are_preserved():
    result = client.post("/api/schema", json={"report_task_nos": [OUTPUT], "pairs": []}).json()
    columns = {column["field_code"]: column for column in result["columns"]}
    assert len(columns) == 8
    assert columns["project_code"]["column_name"] == "项目编码 / 工程编码"
    assert len(columns["project_code"]["definitions"]) == 2
    assert columns["legacy_project_name"]["column_name"] == columns["project_name"]["column_name"] == "项目名称"
    assert columns["legacy_project_name"]["column_id"] != columns["project_name"]["column_id"]
    assert not {"current_only", "deleted_record_only", "deleted_field"} & columns.keys()
    assert all(not column["is_join_key"] for column in columns.values())
    assert columns["completion_rate"]["definitions"][0]["field_kind"] == "DERIVED"


def test_disconnected_or_unlinked_tasks_still_have_headers():
    request = config()
    request["report_task_nos"].append(LABOR)
    request["pairs"] = []
    response = client.post("/api/schema", json=request)
    assert response.status_code == 200
    assert len(response.json()["columns"]) == 29
    assert all(not column["is_join_key"] for column in response.json()["columns"])


def test_schema_never_reads_row_data_and_reuses_metadata(monkeypatch):
    queries = []
    original_connect = db.connect

    class MetadataOnlyCursor(pymysql.cursors.DictCursor):
        def execute(self, query, args=None):
            assert "fi_fill_record_row" not in query.lower()
            assert "row_data" not in query.lower()
            assert query.lstrip().upper().startswith("SELECT")
            queries.append(query)
            return super().execute(query, args)

    def metadata_connection():
        connection = original_connect()
        connection.cursorclass = MetadataOnlyCursor
        return connection

    monkeypatch.setattr(db, "connect", metadata_connection)
    response = client.post("/api/schema", json=config())
    assert response.status_code == 200
    assert len(queries) <= 2
    assert "row_data" not in response.text
    assert "warning_result" not in response.text


def test_no_records_returns_empty_schema_without_current_template_fallback(metadata_transaction):
    with metadata_transaction.cursor() as cursor:
        cursor.execute("UPDATE fi_fill_record SET delete_flag = 1 WHERE report_task_no = %s", (LABOR,))
    result = build_wide_table_schema(metadata_transaction, RelationConfig(report_task_nos=[LABOR], pairs=[]))
    assert result == {"report_task_nos": [LABOR], "pairs": [], "columns": []}


def test_type_conflicts_are_exposed_without_choosing_a_type(metadata_transaction):
    with metadata_transaction.cursor() as cursor:
        cursor.execute("""UPDATE tc_report_field SET data_type = 'NUMBER'
                          WHERE report_template_no = 'TPL_OUTPUT_OLD' AND field_code = 'project_code'""")
    result = build_wide_table_schema(metadata_transaction, RelationConfig(report_task_nos=[OUTPUT], pairs=[]))
    column = next(column for column in result["columns"] if column["field_code"] == "project_code")
    assert column["data_type"] is None
    assert set(column["data_types"]) == {"TEXT", "NUMBER"}
    assert {definition["report_template_no"] for definition in column["definitions"]} == {"TPL_OUTPUT", "TPL_OUTPUT_OLD"}


@pytest.mark.parametrize("change", ["unknown_field", "same_task", "duplicate_pair", "unknown_join", "empty_tasks", "deleted_task"])
def test_schema_revalidates_configuration(change):
    request = config()
    if change == "unknown_field":
        request["pairs"][0]["links"][0]["left_field_code"] = "current_only"
    elif change == "same_task":
        request["pairs"][0]["right_task_no"] = OUTPUT
    elif change == "duplicate_pair":
        request["pairs"].append(deepcopy(request["pairs"][0]))
    elif change == "unknown_join":
        request["pairs"][0]["join_type"] = "CROSS"
    elif change == "empty_tasks":
        request["report_task_nos"] = []
    elif change == "deleted_task":
        request["report_task_nos"].append("DEMO_EXCLUDED_19")
    assert client.post("/api/schema", json=request).status_code == (404 if change == "deleted_task" else 422)


def test_database_error_does_not_return_a_partial_schema(monkeypatch):
    def fail():
        raise pymysql.OperationalError(2003, "private-host private-password")
    monkeypatch.setattr(db, "connect", fail)
    response = client.post("/api/schema", json=config())
    assert response.status_code == 503
    assert "columns" not in response.json()
    assert "private-password" not in response.text
