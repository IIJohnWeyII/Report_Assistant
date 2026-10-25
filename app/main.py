import logging
import os

import pymysql
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from app import db
from app.functions import build_wide_table_schema
from app.llm import generate_analysis
from app.relations import RelationConfig, TaskSelection, get_task_fields, validate_relations

app = FastAPI(title="填报分析助手", version="0.4.0", description="任务、关联、表头与 Ollama SQL 生成。本阶段不执行分析 SQL。")
app.mount("/static", StaticFiles(directory=db.ROOT / "static"), name="static")


@app.exception_handler(pymysql.MySQLError)
def database_error(request, exc):
    logging.getLogger(__name__).error("Database operation failed (%s)", type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": "数据库暂时不可用，请检查连接配置后重试。"})


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(db.ROOT / "static" / "index.html")


@app.get("/api/tasks", summary="按名称查询未删除的已结束任务")
def list_tasks(q: str = Query(default="", max_length=200)):
    with db.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT report_task_no, task_name, task_type, publish_org_name,
                      data_time_value, time_range_start, time_range_end
               FROM rt_report_task
               WHERE task_status = 'COMPLETED' AND delete_flag = 0
                 AND LOCATE(%s, task_name) > 0
               ORDER BY time_range_end DESC, report_task_no ASC""",
            (q.strip(),),
        )
        tasks = cursor.fetchall()
    return {"tasks": tasks, "total": len(tasks), "environment": os.getenv("APP_ENV", "development")}


@app.post("/api/fields", summary="读取所选任务的历史模板字段，不读取填报值")
def list_fields(selection: TaskSelection):
    with db.connect() as connection:
        return {"tasks": get_task_fields(connection, selection.report_task_nos)}


@app.post("/api/relations/validate", summary="校验并返回关联配置，不写库")
def check_relations(config: RelationConfig):
    with db.connect() as connection:
        validate_relations(connection, config)
    return config.model_dump()


@app.post("/api/schema", summary="校验关联并生成宽表表头，不读取填报值")
def build_schema(config: RelationConfig):
    with db.connect() as connection:
        return build_wide_table_schema(connection, config)


class AnalysisRequest(RelationConfig):
    prompt: str = Field(min_length=1, max_length=3000)


@app.post("/api/analysis", summary="根据提示词与服务端表头生成分析 SQL，不执行取数")
def analyze(request: AnalysisRequest):
    if not request.prompt.strip():
        raise HTTPException(422, "请输入分析需求。")
    config = RelationConfig(report_task_nos=request.report_task_nos, pairs=request.pairs)
    with db.connect() as connection:
        schema = build_wide_table_schema(connection, config)
    return generate_analysis(request.prompt.strip(), schema)
