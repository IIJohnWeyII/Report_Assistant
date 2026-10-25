import json

from fastapi import HTTPException
from fastapi.testclient import TestClient
import httpx
import pymysql
import pytest

from app import db, llm
from app.functions import build_sql_catalog, build_wide_table_schema
from app.main import app
from app.relations import RelationConfig

client = TestClient(app)
COST, MATERIAL = 'DEMO_COST_202610', 'DEMO_MATERIAL_202610'
SINGLE_SQL = 'SELECT t0.c1 AS category, SUM(t0.n6) AS value0 FROM t0 GROUP BY t0.c1 ORDER BY value0 DESC'
JOIN_SQL = 'SELECT t0.c1 AS category, SUM(t1.n6) AS value0 FROM t0 LEFT JOIN t1 ON CAST(t0.c0 AS BINARY)=CAST(t1.c0 AS BINARY) AND CAST(t0.c2 AS BINARY)=CAST(t1.c2 AS BINARY) GROUP BY t0.c1'


def result(sql=SINGLE_SQL, chart_type='bar'):
    return llm.ModelAnalysis(status='ok', sql=sql, message='按项目名称汇总成本。', chart=llm.ChartSpec(
        type=chart_type, title='项目成本', category='category',
        series=[llm.Series(column='value0', label='总成本（万元）')]))


def request():
    return {'report_task_nos': [COST], 'pairs': [], 'prompt': '按项目名称汇总项目总成本，使用柱状图。'}


@pytest.fixture
def catalog():
    group = {'left_task_no': COST, 'right_task_no': MATERIAL, 'join_type': 'LEFT', 'links': [
        {'left_field_code': 'project_code', 'right_field_code': 'project_code'},
        {'left_field_code': 'period', 'right_field_code': 'period'}]}
    with db.connect() as connection:
        schema = build_wide_table_schema(connection, RelationConfig(report_task_nos=[COST, MATERIAL], pairs=[group]))
    return build_sql_catalog(schema)


def test_generation_reads_only_metadata_and_returns_real_sql_contract(monkeypatch):
    queries, prompts = [], []
    original_connect = db.connect

    class MetadataCursor(pymysql.cursors.DictCursor):
        def execute(self, query, args=None):
            assert 'row_data' not in query.lower()
            assert 'fi_fill_record_row' not in query.lower()
            queries.append(query)
            return super().execute(query, args)

    def connect():
        connection = original_connect()
        connection.cursorclass = MetadataCursor
        return connection

    def model(messages):
        prompts.append(messages)
        return result(), 'test-model'

    monkeypatch.setattr(db, 'connect', connect)
    monkeypatch.setattr(llm, 'call_model', model)
    response = client.post('/api/analysis', json=request())
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'ok' and data['model'] == 'test-model'
    assert 'SUM(t0.n6)' in data['sql']
    assert data['prepared_sql'].startswith('WITH')
    assert data['sources'][0]['task_no'] == COST
    assert len(queries) == 2
    context = json.loads(prompts[0][1]['content'])
    assert context['catalog']['tables'][0]['fields'][6]['name'] == '项目总成本'
    assert 'row_data' not in prompts[0][1]['content']


def test_scoped_sql_is_valid_mysql_without_executing_analysis(monkeypatch):
    monkeypatch.setattr(llm, 'call_model', lambda messages: (result(), 'test-model'))
    data = client.post('/api/analysis', json=request()).json()
    assert all(f'fi_fill_record_row{i}' in data['prepared_sql'] for i in range(4))
    assert "t.task_status = 'COMPLETED'" in data['prepared_sql']
    assert 't.delete_flag = 0' in data['prepared_sql']
    with db.connect() as connection, connection.cursor() as cursor:
        cursor.execute('EXPLAIN ' + data['prepared_sql'])
        assert cursor.fetchall()


def test_missing_relation_can_be_a_model_clarification(monkeypatch):
    clarification = llm.ModelAnalysis(status='clarify', sql='', chart=None, message='请先配置成本和材料任务之间的关联字段。')
    monkeypatch.setattr(llm, 'call_model', lambda messages: (clarification, 'test-model'))
    payload = {**request(), 'report_task_nos': [COST, MATERIAL]}
    response = client.post('/api/analysis', json=payload)
    assert response.status_code == 200
    assert response.json()['status'] == 'clarify'
    assert response.json()['prepared_sql'] is None


def test_invalid_sql_has_only_one_model_repair_attempt(monkeypatch):
    calls = []
    def model(messages):
        calls.append(len(messages))
        return result('DELETE FROM t0'), 'test-model'
    monkeypatch.setattr(llm, 'call_model', model)
    response = client.post('/api/analysis', json=request())
    assert response.status_code == 422
    assert calls == [2, 4]


def test_model_can_clarify_after_validation_feedback(monkeypatch):
    calls = []
    def model(messages):
        calls.append(messages)
        if len(calls) == 1:
            return result('DELETE FROM t0'), 'test-model'
        assert '服务器校验失败' in messages[-1]['content']
        return llm.ModelAnalysis(status='clarify', sql='', chart=None, message='请补充任务关联。'), 'test-model'
    monkeypatch.setattr(llm, 'call_model', model)
    response = client.post('/api/analysis', json=request())
    assert response.status_code == 200 and response.json()['status'] == 'clarify'
    assert len(calls) == 2


@pytest.mark.parametrize('sql', [
    'DELETE FROM t0', SINGLE_SQL + '; DROP TABLE t0',
    SINGLE_SQL.replace('SUM(t0.n6)', 'SLEEP(1)'),
    SINGLE_SQL.replace('t0.n6', 't0.n99'),
    SINGLE_SQL.replace('SUM(t0.n6)', 'SUM(t0.c6)'),
    SINGLE_SQL.replace('SUM(t0.n6)', 'COUNT(*)'),
    SINGLE_SQL.replace('FROM t0', 'FROM rt_report_task'),
    SINGLE_SQL.replace('FROM t0', 'FROM other_db.t0'),
    SINGLE_SQL.replace('GROUP BY t0.c1', ''),
    SINGLE_SQL.replace('GROUP BY', "WHERE t0.c1 = 'A' GROUP BY"),
    SINGLE_SQL.replace('GROUP BY', 'WHERE t0.c6 > 12 GROUP BY'),
    SINGLE_SQL.replace('GROUP BY', "WHERE t0.n6 = '12x' GROUP BY"),
    SINGLE_SQL + ' FOR UPDATE',
    SINGLE_SQL.replace('SUM(t0.n6)', '(SELECT 1)'),
    JOIN_SQL.replace('LEFT JOIN', 'CROSS JOIN'),
    JOIN_SQL.replace('LEFT JOIN', 'INNER JOIN'),
    JOIN_SQL.replace(' AND CAST(t0.c2 AS BINARY)=CAST(t1.c2 AS BINARY)', ''),
    JOIN_SQL.replace('CAST(t0.c0 AS BINARY)=CAST(t1.c0 AS BINARY)', 't0.c0=t1.c0'),
    'SELECT t1.c1 AS category, SUM(t0.n6) AS value0 FROM t1 LEFT JOIN t0 ON CAST(t0.c0 AS BINARY)=CAST(t1.c0 AS BINARY) AND CAST(t0.c2 AS BINARY)=CAST(t1.c2 AS BINARY) GROUP BY t1.c1',
])
def test_reject_unsafe_or_inconsistent_model_sql(catalog, sql):
    with pytest.raises(HTTPException) as error:
        llm.validate_sql(result(sql), catalog)
    assert error.value.status_code == 422


def test_valid_left_join_preserves_all_conditions(catalog):
    query, used = llm.validate_sql(result(JOIN_SQL), catalog)
    assert used == {'t0', 't1'}
    assert 'LEFT JOIN' in query
    assert query.count('AS BINARY') == 4


def test_filters_preserve_string_and_numeric_semantics(catalog):
    query = SINGLE_SQL.replace('GROUP BY', "WHERE CAST(t0.c1 AS BINARY) = CAST('A ' AS BINARY) AND t0.n6 > 12 GROUP BY")
    sql, _ = llm.validate_sql(result(query), catalog)
    assert "'A '" in sql
    assert 't0.n6 > 12' in sql


def test_negative_numeric_filter_is_supported(catalog):
    sql, _ = llm.validate_sql(result(SINGLE_SQL.replace('GROUP BY', 'WHERE t0.n6 > -12 GROUP BY')), catalog)
    assert 't0.n6 > -12' in sql


def test_catalog_type_conflict_has_no_numeric_projection():
    with db.connect() as connection:
        schema = build_wide_table_schema(connection, RelationConfig(report_task_nos=[COST], pairs=[]))
    schema['columns'][6]['data_type'] = None
    schema['columns'][6]['data_types'] = ['NUMBER', 'TEXT']
    catalog = build_sql_catalog(schema)
    assert catalog['tables'][0]['fields'][6]['number_column'] is None
    with pytest.raises(HTTPException):
        llm.validate_sql(result(), catalog)


@pytest.mark.parametrize('prompt', ['', '   ', 'x' * 3001])
def test_invalid_prompt_does_not_call_model(monkeypatch, prompt):
    def forbidden(messages):
        raise AssertionError('Model must not be called')
    monkeypatch.setattr(llm, 'call_model', forbidden)
    response = client.post('/api/analysis', json={**request(), 'prompt': prompt})
    assert response.status_code == 422


def test_transport_uses_config_and_structured_output(monkeypatch):
    monkeypatch.setenv('LLM_BASE_URL', 'http://localhost:11434')
    monkeypatch.setenv('LLM_MODEL', 'configured-model')
    monkeypatch.setenv('LLM_API_KEY', 'test-secret')
    def post(url, **kwargs):
        assert url == 'http://localhost:11434/api/chat'
        assert kwargs['json']['model'] == 'configured-model'
        assert kwargs['json']['stream'] is False
        assert kwargs['json']['format']['properties']['status']
        assert kwargs['headers']['Authorization'] == 'Bearer test-secret'
        assert 'test-secret' not in json.dumps(kwargs['json'])
        return httpx.Response(200, json={'done': True, 'message': {'content': result().model_dump_json()}}, request=httpx.Request('POST', url))
    monkeypatch.setattr(llm.httpx, 'post', post)
    response, model = llm.call_model([{'role': 'user', 'content': 'test'}])
    assert response.status == 'ok' and model == 'configured-model'


@pytest.mark.parametrize('mode,code', [('timeout', 504), ('unavailable', 503), ('bad_json', 502), ('incomplete', 502)])
def test_transport_failures_are_actionable_and_do_not_leak_secrets(monkeypatch, mode, code):
    def post(url, **kwargs):
        if mode == 'timeout':
            raise httpx.ReadTimeout('private-key')
        if mode == 'unavailable':
            raise httpx.ConnectError('private-key')
        return httpx.Response(200, json={'done': mode != 'incomplete', 'message': {'content': 'bad-json'}}, request=httpx.Request('POST', url))
    monkeypatch.setattr(llm.httpx, 'post', post)
    with pytest.raises(HTTPException) as error:
        llm.call_model([])
    assert error.value.status_code == code
    assert 'private-key' not in error.value.detail
