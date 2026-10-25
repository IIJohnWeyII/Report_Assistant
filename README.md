# 填报分析助手

本地连库 MVP，原生 HTML/CSS/JavaScript + FastAPI + PyMySQL。没有 ORM、前端构建系统或额外业务表。

**继续开发请先阅读 [项目进度与交接指南](docs/HANDOFF.md)。** 当前功能 1–3 已人工验收，功能 4 待人工验收，实际查询与绘图尚未实施。

## 当前验收点

功能 1（任务筛选）和功能 2（字段与关联配置，包括单页简化修订）已由用户人工验收通过。
功能 3（宽表表头）已由用户人工验收通过。功能 4（Ollama 根据提示词生成 SQL）已实现，等待人工验收。
界面保持单页左右工作区：左侧选任务，右侧配置关系，点击“生成宽表表头”后在页签内查看结果。
生成表头后可切换到“智能分析”，输入提示词生成 SQL；底部保留一个随页签切换的主按钮。当前不执行分析 SQL，也不展示数据或图表。

## 本地启动（PowerShell）

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/local_db.py setup
.\scripts\start.ps1
```

首次 setup 下载并校验官方 MySQL ZIP，初始化项目内 `.runtime/mysql-data`，生成独立账号和 `.env`。
不注册 Windows 服务；MySQL 仅监听 `127.0.0.1:13306`，应用仅监听 `127.0.0.1:8000`。
后续启动数据库使用 `.\.venv\Scripts\python.exe scripts/local_db.py start`，然后运行 `scripts/start.ps1`。
停止网页服务按 Ctrl+C；停止数据库使用 `.\.venv\Scripts\python.exe scripts/local_db.py stop`。

- 页面：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 接口：`GET /api/tasks?q=产值`，返回 `tasks`、`total`、`environment`。
- 字段：`POST /api/fields`，请求 `{"report_task_nos": ["DEMO_OUTPUT_202610"]}`。
- 关联：`POST /api/relations/validate`，校验并原样返回结构化配置，不写库、不取填报值。
- 表头：`POST /api/schema`，请求体与关联配置一致，返回 `report_task_nos`、`pairs`、`columns`。
- 模型分析：`POST /api/analysis`，请求为所选任务、关联配置和 prompt；返回 SQL、完整限定范围 SQL、图表建议与字段映射。当前不执行分析取数。
- 请求/响应结构与错误状态可在 API 文档中直接联调；当前配置保存在页面内，刷新即清空。

## 数据与生产配置

`docs/source-ddl.sql` 是用户提供的原始 DDL，不修改字段；row1–row3 根据 row0 创建。
测试任务为 2026 年 7–10 月的产值、成本、材料、劳务四类，共 16 条已结束任务。
另外 3 条用于验证进行中、草稿、已删除任务被排除。月份只是筛选样例，不代表真实已发生业务。
已补充项目编码、数据月份、产值、成本、材料、劳务等字段及记录元信息。没有填报行数据。
产值 10 月任务包含历史模板：项目编码的历史名称“工程编码”保留，同名“项目名称”按不同编码区分。
当前模板上未用于填报的字段、已删记录独有字段、已删除字段不进入字段选项。
记录元信息样例不指定尚未确认的 fill_status / audit_status，不用这些样例冒充已提交业务数据。

应用查询条件固定为 `task_status = 'COMPLETED' AND delete_flag = 0`，展示发布组织和数据时间，不读取行数据。
搜索参数绑定，不将输入拼进 SQL；列表字段通过 textContent 渲染；连接账号只有 SELECT 权限。
字段查询先去重“任务＋记录模板”，再读取字段定义，避免同一模板因多份记录重复展开。
此阶段以未删除记录枚举历史字段，取数阶段的有效填报状态需另行确认；不读取当前模板作为替代来源。

## 关联配置契约

```json
{
  "report_task_nos": ["DEMO_OUTPUT_202610", "DEMO_COST_202610"],
  "pairs": [{
    "left_task_no": "DEMO_OUTPUT_202610",
    "right_task_no": "DEMO_COST_202610",
    "join_type": "LEFT",
    "links": [
      {"left_field_code": "project_code", "right_field_code": "project_code"},
      {"left_field_code": "period", "right_field_code": "period"}
    ]
  }]
}
```

同一任务对只建一组（交换左右仍视为同一对），多个条件放在 links 内且全部满足。
LEFT 保留 left_task_no 一侧，交换左右时对应字段同步交换，连接类型保持不变。
服务器只校验任务/字段归属、结构与重复项；不判断字段类型是否适合对齐，不检查整体连通或自动增加关系。
单任务以及多任务部分关联均可确认；这些关系能否满足具体需求留到分析阶段判断。
任务选择修改后自动刷新字段，立即移除涉及已取消任务的组，其余组保留。
字段加载期间或失败时暂停关系编辑与确认，失败时提供重试；搜索及“全部/已选”切换不更改配置。

切换生产时仅修改 `.env` 中的 DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD，APP_ENV 改为 production。
支持 DB_SSL_CA 指定 CA 文件并校验服务端证书与主机名；数据库环境变量优先于 `.env`。
重启网页服务即可。生产应提供只读账号和相同表结构。正常应用启动不执行 setup、DDL 或测试数据写入。
测试初始化固定指向项目内本地实例，不读取应用数据库配置。测试只允许本地 demo 配置。
当前接口已在本地 MySQL 验证；真实 TiDB 连通与兼容性需要生产配置到位后验证。
只读约束由数据库 SELECT 权限保证，不使用 TiDB 默认不支持的 SET TRANSACTION READ ONLY。
兼容性依据：[TiDB 的 noop 系统变量说明](https://docs.pingcap.com/tidb/stable/system-variables#tidb_enable_noop_functions-new-in-v40)。

## 测试与人工验收

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
```

1. 首页显示 16 个已结束任务；查不到“进行中测试任务”“草稿测试任务”“已删除测试任务”。
2. 搜索“产值”，应出现 4 个任务；搜索不存在的名称应显示空状态。
3. 左侧勾选两个任务，“已选”计数同步变化，右侧自动读取字段。
4. 在搜索条件变化后已选任务仍保留；“选择当前结果”只作用于当前搜索结果。
5. “已选”筛选可以集中查看、取消选择；“清空选择”移除所有任务、关系及已生成表头。
6. 未选任务时生成按钮禁用；JSON 默认收起，包含真实 report_task_no。
7. 桌面任务列表与关联列表分别滚动，底部确认按钮固定可见；功能 1 的验收记录见 docs/acceptance-01.md。

功能 2 人工验收建议：

1. 勾选 10 月产值、成本、材料三个任务，字段自动加载，无需额外确认。
2. 添加“产值 LEFT JOIN 成本”，用项目编码和数据月份添加两条 AND 条件。
3. 检查产值字段包含“项目编码 / 工程编码”，两个“项目名称”可按编码区分。
4. 交换左右，检查保留侧提示及字段随之交换；切换 INNER 后再次确认。
5. 添加另一组关系；测试删除条件、删除整组和重复任务对提示。
6. 取消勾选一个任务，自动移除相关关联组，其他组与条件保留。
7. 单任务无需关系；多任务可只配置部分关系；打开 JSON 检查 join_type、左右任务与 links。
8. 当前界面通过“生成宽表表头”统一校验关联，功能 2 的独立校验 API 保留。

功能 3 人工验收建议：

1. 单选 10 月产值任务，生成 8 列表头，所有字段均不标为关联键。
2. 选择产值、成本、材料三个任务：产值 LEFT 成本按项目编码和数据月份关联，产值 INNER 材料按项目编码关联。应生成 22 列、5 个关联键。
3. 检查历史名称“项目编码 / 工程编码”；同名“项目名称”仍有独立的列标识。
4. 在“任务关联/宽表表头”之间切换。修改任务、连接类型或字段条件后，旧表头被清除，需重新生成；仅搜索不影响已有表头。
5. 展开表头 JSON，确认仅包含元信息与关联配置，没有填报值。

## 表头接口说明

`app/functions.py` 的 `build_wide_table_schema` 复用关联校验取得的元信息，整个请求只执行两次 SELECT：任务资格和历史模板字段。
不访问四张行表、不读取 row_data、不执行 JOIN 取数，也不创建物理宽表。
列标识固定为 `{report_task_no}.{field_code}`，同一任务的同一编码合并为一列，保留所有历史字段定义。
每列包含 column_id、column_name、report_task_no、task_name、field_code、data_type、data_types、is_join_key、definitions。
同编码类型一致时 data_type 为原类型；存在历史类型冲突时 data_type 为 null，data_types 和 definitions 保留各类型，并在界面提示。
关联键标记取自配置中所有组的两侧字段；表头阶段不检查关系整体连通性，未关联任务也会生成列。
生成或重新生成失败时不显示旧结果。任务或关联变化会使已生成表头失效，迟到的旧请求不会覆盖新配置。

## 后续需求边界

最新验收顺序：任务筛选（通过）→ 字段与关联配置（通过）→ 宽表表头（通过）→ 内部模型根据提示词生成 SQL → SQL 校验与查询 → 图表。后续功能仍逐项自动测试、用户人工验收后推进。
用户已取消手工填写分析 JSON 的路线，采用“提示词＋已生成表头 JSON＋已有关系 → 内部模型生成分析 SQL → 服务端校验并执行 → 绘图”。模型可返回必要的图表配置，但不要求用户手工填写。
关系配置阶段不要求整体连通；需求分析阶段判断所需任务路径。支持 INNER/LEFT JOIN，LEFT 明确保留方向。
模型结合提示词选取本次所需任务和已有关联路径，不自动补关联；路径存在业务歧义时向用户澄清。保留 SQL 多对多组合语义。图表只展示一张，可换类型与数据字段。
填报记录先按 delete_flag=0 读取，不限制 fill_status/audit_status。历史类型冲突字段可作为维度或字符串关联字段，不能直接作为数值度量。
模型 SQL 经只读及数据范围校验后自动执行，页面可折叠查看 SQL；连接账号继续仅拥有 SELECT 权限。
当前临时使用本机 Ollama 的 qwen3.6-35b:latest。公司接口到位后替换模型传输函数；密钥仅存放本地配置，不写入代码或文档。

环境安装依据：[MySQL 官方 ZIP 下载](https://dev.mysql.com/downloads/mysql/8.4.html?os=3)、[Windows 手动安装](https://dev.mysql.com/doc/refman/8.4/en/windows-install-archive.html)。


## 本地模型与后续替换

`.env.example` 包含 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY、LLM_TIMEOUT_SECONDS。
当前默认调用 `http://127.0.0.1:11434/api/chat`，关闭流式与思考输出，要求结构化返回。
无需 Ollama Python SDK，复用 httpx。模型接口集中在 `app/llm.py` 的 `call_model`。
公司接口若兼容 Ollama 可直接改配置；协议不同时只替换 call_model，使其继续返回 ModelAnalysis 和模型名，界面与 SQL 规则无需改动。
默认超时 240 秒；首次加载可能较慢。本次实测冷启动约 75 秒，加载后典型请求约 3–6 秒，仅供本机参考。

模型接收从服务端重建表头得到的精简字段目录、可用关系与提示词，不接收 row_data 或填报值。
返回 status=ok 时包含 sql 和 chart；需求不明确或关系不足时返回 status=clarify、空 SQL 和中文问题。
SQL 用 t0/t1 等逻辑表和 cN/nN 列引用；sources 提供它们与业务任务、字段的映射。
cN 保留原始字符串；nN 仅用于 NUMBER 字段，非法数字为空值，不按 0 处理。
完整只读 SQL 由服务端补充 CTE，固定限定所选且未删除的已结束任务、未删除记录和四张未删除行分表；不创建物理宽表。
普通小数使用 DECIMAL(38,10) 数值投影，最多 28 位整数、10 位小数，不接受科学计数法或千分位格式；此处仅生成投影 SQL，不执行转换。
SQLGlot 解析限制为一条聚合 SELECT，验证表和列白名单、NUMBER 聚合、图表输出绑定、完整关联条件、BINARY 字符串比较和 LEFT 方向。
生成结果未通过校验时，最多向模型反馈一次进行纠正；仍失败则返回错误，不执行或展示为有效 SQL。
模型生成期间可以修改配置或提示词，迟到结果会被丢弃。该接口不执行分析查询；自动测试仅用 EXPLAIN 验证完整 SQL 可被 MySQL 解析。

### 功能 4 人工验收

1. 选择 10 月“项目成本月报”，生成表头，再打开“智能分析”。
2. 输入“按项目名称汇总项目总成本，使用柱状图。”，点击“生成分析 SQL”。
3. 检查真实模型名、SQL 的分组与 SUM、图表建议；可展开字段映射及完整只读 SQL。
4. 修改提示词，旧结果应立即隐藏；修改任务或关系后，需重新生成表头才能再次分析。
5. 选择成本和材料两个任务但不配关系，提示词要求对比两者指标，模型应要求补充关联，或由服务器明确拦截无效关系。
6. 空提示词有提示；模型服务不可用、超时或返回格式错误时有明确错误，不使用模拟结果替代。
7. 本阶段不执行分析 SQL。用户明确通过后，才实现实际查询与绘图。

技术依据：[Ollama Chat API](https://docs.ollama.com/api/chat)、[结构化返回](https://docs.ollama.com/capabilities/structured-outputs)、[SQLGlot AST](https://sqlglot.com/sqlglot/expressions.html)。
