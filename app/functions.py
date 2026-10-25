"""Analysis functions shared by HTTP endpoints and the model adapter."""
import json
from app.relations import validate_relations


def build_wide_table_schema(connection, config):
    tasks = validate_relations(connection, config)
    join_keys = set()
    for pair in config.pairs:
        for link in pair.links:
            join_keys.add((pair.left_task_no, link.left_field_code))
            join_keys.add((pair.right_task_no, link.right_field_code))

    columns = []
    for task in tasks:
        task_no = task["report_task_no"]
        for field in task["fields"]:
            definitions = field["definitions"]
            names = list(dict.fromkeys(item["field_name"] for item in definitions))
            types = list(dict.fromkeys(item["data_type"] for item in definitions))
            columns.append({
                "column_id": f"{task_no}.{field['field_code']}",
                "column_name": " / ".join(names),
                "report_task_no": task_no,
                "task_name": task["task_name"],
                "field_code": field["field_code"],
                "data_type": types[0] if len(types) == 1 else None,
                "data_types": types,
                "is_join_key": (task_no, field["field_code"]) in join_keys,
                "definitions": definitions,
            })
    return {**config.model_dump(), "columns": columns}


def build_sql_catalog(schema):
    tables, locations = [], {}
    for task_no in schema["report_task_nos"]:
        columns = [c for c in schema["columns"] if c["report_task_no"] == task_no]
        table = {"alias": f"t{len(tables)}", "task_no": task_no,
                 "task_name": columns[0]["task_name"] if columns else task_no, "fields": []}
        for index, column in enumerate(columns):
            field = {"column_id": column["column_id"], "field_code": column["field_code"],
                     "name": column["column_name"], "text_column": f"c{index}",
                     "number_column": f"n{index}" if column["data_type"] == "NUMBER" else None,
                     "data_types": column["data_types"],
                     "units": list(dict.fromkeys(d["unit"] for d in column["definitions"] if d["unit"]))}
            table["fields"].append(field)
            locations[(task_no, column["field_code"])] = (table["alias"], field["text_column"])
        tables.append(table)
    pairs = []
    for pair in schema["pairs"]:
        conditions = []
        for link in pair["links"]:
            left = locations[(pair["left_task_no"], link["left_field_code"])]
            right = locations[(pair["right_task_no"], link["right_field_code"])]
            conditions.append([left[1], right[1]])
        pairs.append({"left": left[0], "right": right[0], "join_type": pair["join_type"], "conditions": conditions})
    return {"tables": tables, "pairs": pairs}


def build_scoped_sql(catalog, used_tables, analysis_sql):
    """Bind logical tables to undeleted task rows, without executing SQL."""
    def text(value):
        return f"CONVERT(0x{value.encode('utf-8').hex()} USING utf8mb4)"

    tables = [t for t in catalog["tables"] if t["alias"] in used_tables]
    task_nos = ", ".join(text(t["task_no"]) for t in tables)
    ctes = [f"_records AS (SELECT r.fill_record_no, r.report_task_no FROM fi_fill_record r JOIN rt_report_task t ON t.report_task_no = r.report_task_no WHERE r.delete_flag = 0 AND t.delete_flag = 0 AND t.task_status = 'COMPLETED' AND r.report_task_no IN ({task_nos}))"]
    rows = [f"SELECT r.report_task_no, d.row_data FROM _records r JOIN fi_fill_record_row{i} d ON d.fill_record_no = r.fill_record_no WHERE d.delete_flag = 0" for i in range(4)]
    ctes.append("_rows AS (" + " UNION ALL ".join(rows) + ")")
    pattern = text(r"^[+-]?([0-9]{1,28}([.][0-9]{0,10})?|[.][0-9]{1,10})$")
    for table in tables:
        values, numbers = [], []
        for field in table["fields"]:
            path = text("$." + json.dumps(field["field_code"], ensure_ascii=False))
            value = f"JSON_EXTRACT(row_data, {path})"
            values.append(f"CASE WHEN JSON_TYPE({value}) = 'NULL' THEN NULL ELSE JSON_UNQUOTE({value}) END AS {field['text_column']}")
            if field["number_column"]:
                source = f"TRIM({field['text_column']})"
                numbers.append(f"CASE WHEN {source} REGEXP {pattern} THEN CAST({source} AS DECIMAL(38,10)) ELSE NULL END AS {field['number_column']}")
        alias = table["alias"]
        ctes.append(f"_{alias}_text AS (SELECT {', '.join(values)} FROM _rows WHERE report_task_no = {text(table['task_no'])})")
        projection = [f["text_column"] for f in table["fields"]] + numbers
        ctes.append(f"{alias} AS (SELECT {', '.join(projection)} FROM _{alias}_text)")
    return "WITH\n" + ",\n".join(ctes) + "\n" + analysis_sql
