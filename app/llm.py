"""Replace call_model when the company API arrives; generation rules stay here."""
import json
import os
from typing import Literal

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
import sqlglot
from sqlglot import exp

from app.functions import build_scoped_sql, build_sql_catalog


class Series(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str
    label: str


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bar", "line", "pie"]
    title: str
    category: Literal["category"]
    series: list[Series] = Field(min_length=1)


class ModelAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok", "clarify"]
    sql: str
    chart: ChartSpec | None
    message: str


SYSTEM_PROMPT = """你是建筑业务填报分析助手。catalog 是由已生成的表头 JSON 得到的权威字段和关联元信息，不含填报值。
返回指定 JSON，不要 Markdown。status=ok 时返回 MySQL SELECT 和 chart；需求有歧义或不能满足时 status=clarify，sql为空字符串，chart=null，message用中文提出具体问题。
SQL 规则：
1. 只使用 catalog 中逻辑表 t0/t1 等。服务端将其限定到所选任务的未删除记录。不要访问物理表，不要写 WITH、子查询、UNION 或表别名。
2. text_column 是原始字符串，number_column 才是可聚合的 NUMBER，非法数字为 NULL。不能重算公式。类型冲突字段没有 number_column，不能聚合。
3. 恰好一个维度：直接选一个 text_column AS category。一个或多个度量必须是 SUM(number_column)、AVG(number_column) 或 COUNT(number_column)，别名 value0、value1 等。禁止 COUNT(*)、COALESCE 和其它计算。
4. GROUP BY 同一个维度。ORDER BY 可使用输出别名。chart.category="category"；series.column 对应度量别名，label 用中文业务名并带单位。饼图只能一个度量，未指定图表默认 bar。
5. 仅使用问题必要的任务。多任务只能使用 catalog.pairs 中关系，每个任务出现一次。JOIN 必须完全使用该组全部 conditions，以 AND 连接，不能增加或省略条件。
6. JOIN 字符串相等写成 CAST(t0.c0 AS BINARY) = CAST(t1.c0 AS BINARY)，保持大小写和前后空格。INNER 可交换左右；LEFT 必须保留已配置 left 表，不能反转保留侧。
7. 禁止 CROSS/RIGHT/FULL JOIN、自动补关联或编造字段。缺少路径或多条路径业务含义不明时返回 clarify，不得删去所需任务来凑出查询。
8. WHERE 筛选在聚合前：字符串 eq/contains，数值 eq/gt/lt。字符串精确比较使用 BINARY，数值使用 number_column。无筛选不要添加，不擅自限制时间。
9. 你看不到填报值，不报告任何具体数字。message 用业务名称简要说明维度、度量、关系；status=clarify 时说明缺少什么。面向用户的说明不要提 catalog、pairs、逻辑表别名或字段别名。
示例 SQL：SELECT t0.c1 AS category, SUM(t0.n6) AS value0 FROM t0 GROUP BY t0.c1 ORDER BY value0 DESC
"""


def call_model(messages):
    """Ollama transport; credentials are never included in model prompts."""
    base = os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("LLM_MODEL", "qwen3.6-35b:latest")
    key = os.getenv("LLM_API_KEY", "")
    try:
        response = httpx.post(base + "/api/chat", json={
            "model": model, "messages": messages, "stream": False, "think": False,
            "format": ModelAnalysis.model_json_schema(), "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 1600},
        }, headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "240")), trust_env=False)
        response.raise_for_status()
        data = response.json()
        if not data.get("done") or data.get("done_reason") == "length":
            raise ValueError("incomplete response")
        return ModelAnalysis.model_validate_json(data["message"]["content"]), model
    except httpx.TimeoutException:
        raise HTTPException(504, "模型响应超时，请稍后重试；首次加载本地模型可能较慢。") from None
    except httpx.HTTPError:
        raise HTTPException(503, "无法调用本地模型，请检查 Ollama 服务、模型名称与连接配置。") from None
    except (ValueError, KeyError, TypeError, ValidationError):
        raise HTTPException(502, "模型返回格式不完整，请重新生成。") from None


def validate_sql(analysis, catalog):
    def reject(message):
        raise HTTPException(422, "模型 SQL 未通过校验：" + message)

    try:
        statements = sqlglot.parse(analysis.sql, read="mysql")
    except sqlglot.errors.SqlglotError:
        reject("SQL 语法无法解析，请重新生成。")
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        reject("只允许一条 SELECT。")
    tree = statements[0]
    allowed = (exp.Select, exp.Alias, exp.Column, exp.Identifier, exp.Table, exp.From, exp.Join,
               exp.EQ, exp.GT, exp.LT, exp.And, exp.Or, exp.Paren, exp.Cast, exp.DataType,
               exp.Sum, exp.Avg, exp.Count, exp.Group, exp.Order, exp.Ordered, exp.Where,
               exp.Like, exp.Literal, exp.Neg, exp.Limit, exp.Null, exp.Is, exp.Not, exp.Boolean)
    if any(not isinstance(node, allowed) for node in tree.walk()) or len(list(tree.find_all(exp.Select))) != 1:
        reject("包含当前 MVP 不支持的表达式、函数或子查询。")
    tables = {table["alias"]: table for table in catalog["tables"]}
    used = set()
    for table in tree.find_all(exp.Table):
        if table.name not in tables or table.db or table.catalog or table.alias or table.name in used or not tables[table.name]["fields"]:
            reject("表来源无效、重复或超出所选任务。")
        used.add(table.name)
    if not used or not tree.args.get("from_") or not isinstance(tree.args["from_"].this, exp.Table):
        reject("缺少有效主任务。")
    chart = analysis.chart
    if not chart or chart.category != "category" or (chart.type == "pie" and len(chart.series) != 1):
        reject("图表类别或度量数量不正确。")
    names = ["category"] + [series.column for series in chart.series]
    if len(set(names)) != len(names) or [item.alias for item in tree.expressions] != names:
        reject("SQL 输出列必须与图表维度、度量逐项对应。")
    for column in tree.find_all(exp.Column):
        if not column.table:
            if column.name in names and column.find_ancestor(exp.Order, exp.Group):
                continue
            reject("字段必须明确所属逻辑表。")
        valid = {name for field in tables.get(column.table, {}).get("fields", [])
                 for name in [field["text_column"], field["number_column"]] if name}
        if column.table not in used or column.name not in valid or column.db or column.catalog:
            reject("引用了表头中不存在的字段。")
    dimension = tree.expressions[0].this
    if not isinstance(dimension, exp.Column) or not dimension.name.startswith("c"):
        reject("维度必须是表头中的一个原始字段。")
    group = tree.args.get("group")
    if not group or len(group.expressions) != 1 or group.expressions[0] not in (dimension, exp.column("category")):
        reject("必须按同一个维度分组。")
    for item in tree.expressions[1:]:
        aggregate = item.this
        if not isinstance(aggregate, (exp.Sum, exp.Avg, exp.Count)) or not isinstance(aggregate.this, exp.Column) or not aggregate.this.name.startswith("n"):
            reject("度量只允许对 NUMBER 字段执行 sum、avg 或 count。")
    for cast in tree.find_all(exp.Cast):
        if cast.args["to"].this != exp.DataType.Type.BINARY or not isinstance(cast.this, (exp.Column, exp.Literal)):
            reject("只允许用于字符串比较的 BINARY 转换。")

    def check_filter(node):
        if isinstance(node, exp.Paren):
            return check_filter(node.this)
        if isinstance(node, (exp.And, exp.Or)):
            check_filter(node.this)
            return check_filter(node.expression)
        if isinstance(node, exp.Is) and isinstance(node.this, exp.Column) and isinstance(node.expression, exp.Null):
            return
        if not isinstance(node, (exp.EQ, exp.GT, exp.LT, exp.Like)):
            reject("筛选只支持 eq、gt、lt、contains 或空值检查。")
        left, right = node.this, node.expression
        def number(value):
            value = value.this if isinstance(value, exp.Neg) else value
            return isinstance(value, exp.Literal) and not value.is_string
        numeric = ((isinstance(left, exp.Column) and left.name.startswith("n") and number(right))
                   or (isinstance(right, exp.Column) and right.name.startswith("n") and number(left)))
        if numeric and isinstance(node, (exp.EQ, exp.GT, exp.LT)):
            return
        binary_text = (isinstance(left, exp.Cast) and isinstance(left.this, exp.Column) and left.this.name.startswith("c")
                       and isinstance(right, exp.Cast) and isinstance(right.this, exp.Literal) and right.this.is_string)
        if binary_text and isinstance(node, (exp.EQ, exp.Like)):
            return
        reject("数值筛选必须用数值列；字符串筛选必须使用 BINARY 字段与字符串常量。")

    if tree.args.get("where"):
        check_filter(tree.args["where"].this)
    joined = {tree.args["from_"].this.name}
    for join in tree.args.get("joins", []):
        target = join.this.name
        kind = (join.side or join.kind or "INNER").upper()
        if kind not in ("INNER", "LEFT") or join.args.get("using") or join.args.get("method"):
            reject("连接方式不受支持。")

        def conditions(node):
            if isinstance(node, exp.Paren):
                return conditions(node.this)
            if isinstance(node, exp.And):
                return conditions(node.this) + conditions(node.expression)
            if not isinstance(node, exp.EQ):
                reject("每组关联只允许 AND 连接的相等条件。")
            columns = []
            for side in (node.this, node.expression):
                if not isinstance(side, exp.Cast) or side.args["to"].this != exp.DataType.Type.BINARY or not isinstance(side.this, exp.Column):
                    reject("关联字段必须按 BINARY 字符串精确比较。")
                columns.append((side.this.table, side.this.name))
            return [frozenset(columns)]

        if join.args.get("on") is None:
            reject("不能使用无条件连接。")
        actual = conditions(join.args["on"])
        matched = False
        for pair in catalog["pairs"]:
            expected = [frozenset(((pair["left"], left), (pair["right"], right))) for left, right in pair["conditions"]]
            direction = (pair["left"] in joined and pair["right"] == target) or (kind == "INNER" and pair["right"] in joined and pair["left"] == target)
            if pair["join_type"] == kind and direction and len(actual) == len(expected) and set(actual) == set(expected):
                matched = True
                break
        if not matched:
            reject("连接方向或字段条件与用户配置不一致。")
        joined.add(target)
    if joined != used:
        reject("存在未连接的数据源。")
    return tree.sql(dialect="mysql", pretty=True, comments=False), used


def generate_analysis(prompt, schema):
    catalog = build_sql_catalog(schema)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps({"prompt": prompt, "catalog": catalog}, ensure_ascii=False)},
    ]
    for attempt in range(2):
        result, model = call_model(messages)
        if result.status == "clarify":
            if result.sql.strip() or result.chart is not None or not result.message.strip():
                raise HTTPException(502, "模型未能明确说明所需的补充信息，请重新生成。")
            return {**result.model_dump(), "model": model, "prepared_sql": None, "sources": []}
        try:
            query, used = validate_sql(result, catalog)
        except HTTPException as error:
            if attempt:
                raise
            messages.extend([
                {"role": "assistant", "content": result.model_dump_json()},
                {"role": "user", "content": f"服务器校验失败：{error.detail}。请基于原始需求和 catalog 修正，禁止补造关系或省略原始需求。若缺少可用关联路径，请返回 status=clarify、sql为空、chart=null，向用户说明要补充的关联配置。"},
            ])
            continue
        return {**result.model_dump(), "sql": query,
                "prepared_sql": build_scoped_sql(catalog, used, query), "model": model,
                "sources": [table for table in catalog["tables"] if table["alias"] in used]}
