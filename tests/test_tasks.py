"""Integration tests against the isolated MySQL demo, plus failure handling."""
import pymysql
import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)


def test_only_completed_non_deleted_tasks():
    response = client.get("/api/tasks")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 16
    assert not any("EXCLUDED" in item["report_task_no"] for item in payload["tasks"])
    assert all(item["publish_org_name"] and item["data_time_value"] for item in payload["tasks"])
    assert all("row_data" not in item for item in payload["tasks"])
    assert payload["tasks"][0]["data_time_value"] == "2026-10"


@pytest.mark.parametrize("query,count", [("产值", 4), (" 材料 ", 4), ("不存在的任务", 0),
                                        ("%' OR 1=1 --", 0), ("%", 0), ("_", 0), ("", 16)])
def test_search(query, count):
    response = client.get("/api/tasks", params={"q": query})
    assert response.status_code == 200
    assert response.json()["total"] == count


def test_reject_oversized_search():
    assert client.get("/api/tasks", params={"q": "a" * 201}).status_code == 422


def test_database_error_is_actionable_and_does_not_leak_credentials(monkeypatch):
    def fail():
        raise pymysql.OperationalError(2003, "internal-host secret-password")
    monkeypatch.setattr(db, "connect", fail)
    response = client.get("/api/tasks")
    assert response.status_code == 503
    assert "数据库" in response.json()["detail"]
    assert "secret-password" not in response.text


def test_database_account_cannot_write():
    with db.connect() as connection, connection.cursor() as cursor:
        with pytest.raises(pymysql.MySQLError) as error:
            cursor.execute("UPDATE rt_report_task SET task_name = task_name WHERE 1 = 0")
        assert error.value.args[0] == 1142


def test_page_and_public_api_contract():
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert "/api/tasks" in client.get("/openapi.json").json()["paths"]
