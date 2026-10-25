"""Field metadata and pair configuration; deliberately no row-data queries here."""
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskNo = Annotated[str, Field(min_length=1, max_length=32)]
FieldCode = Annotated[str, Field(min_length=1, max_length=100)]


class TaskSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_task_nos: list[TaskNo] = Field(min_length=1)

    @field_validator("report_task_nos")
    @classmethod
    def unique_tasks(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("所选任务不能重复")
        return values


class FieldLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_field_code: FieldCode
    right_field_code: FieldCode


class TaskPair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_task_no: TaskNo
    right_task_no: TaskNo
    join_type: Literal["INNER", "LEFT"]
    links: list[FieldLink] = Field(min_length=1)


class RelationConfig(TaskSelection):
    pairs: list[TaskPair]


def get_task_fields(connection, task_nos):
    placeholders = ",".join(["%s"] * len(task_nos))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT report_task_no, task_name FROM rt_report_task
                WHERE report_task_no IN ({placeholders})
                  AND task_status = 'COMPLETED' AND delete_flag = 0""", task_nos)
        tasks = {row["report_task_no"]: {**row, "fields": []} for row in cursor.fetchall()}
        if len(tasks) != len(task_nos):
            raise HTTPException(404, "所选任务不存在、已删除或尚未结束，请重新选择。")
        # Historical templates come exclusively from undeleted fill-record metadata.
        # fill/audit status rules affect later data retrieval and are not guessed here.
        cursor.execute(
            f"""SELECT r.report_task_no, f.report_template_no, f.field_code,
                       f.field_name, f.data_type, f.field_kind, f.unit, f.sort_no
                FROM (SELECT DISTINCT report_task_no, report_template_no
                      FROM fi_fill_record
                      WHERE report_task_no IN ({placeholders}) AND delete_flag = 0) r
                JOIN tc_report_field f ON f.report_template_no = r.report_template_no
                WHERE f.delete_flag = 0
                ORDER BY r.report_task_no, f.sort_no, f.field_code, f.report_template_no""", task_nos)
        fields = {}
        for row in cursor.fetchall():
            task_no = row.pop("report_task_no")
            field_code = row.pop("field_code")
            row.pop("sort_no")
            key = (task_no, field_code)
            if key not in fields:
                fields[key] = {"field_code": field_code, "definitions": []}
                tasks[task_no]["fields"].append(fields[key])
            fields[key]["definitions"].append(row)
    return [tasks[task_no] for task_no in task_nos]


def validate_relations(connection, config):
    metadata = get_task_fields(connection, config.report_task_nos)
    tasks = {task["report_task_no"]: {f["field_code"] for f in task["fields"]}
             for task in metadata}
    seen_pairs = set()
    for number, pair in enumerate(config.pairs, 1):
        left, right = pair.left_task_no, pair.right_task_no
        if left == right:
            raise HTTPException(422, f"关联组 {number}：请选择两个不同任务。")
        if left not in tasks or right not in tasks:
            raise HTTPException(422, f"关联组 {number}：两侧任务必须在所选任务中。")
        key = frozenset((left, right))
        if key in seen_pairs:
            raise HTTPException(422, f"关联组 {number}：同一对任务只能有一组，请在已有组内添加条件。")
        seen_pairs.add(key)
        seen_links = set()
        for link in pair.links:
            if link.left_field_code not in tasks[left] or link.right_field_code not in tasks[right]:
                raise HTTPException(422, f"关联组 {number}：字段不存在于对应任务的历史模板中，请重新选择。")
            key = (link.left_field_code, link.right_field_code)
            if key in seen_links:
                raise HTTPException(422, f"关联组 {number}：字段条件不能重复。")
            seen_links.add(key)
    # No connectivity or type-compatibility check: users define the relations.
    return metadata
