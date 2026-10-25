# 项目进度与继续指南

保存日期：2026-10-25。计划于 2026-10-26 继续。

指定 GitHub 仓库：https://github.com/IIJohnWeyII/Report_Assistant
默认开发分支：main。新机器先 git clone 该仓库，再按下文重建环境。

## 从这里恢复

先阅读本文件，再阅读 README.md 和 docs/acceptance-04.md。
项目当前位于功能 4 的人工验收关口，尚未获用户通过。不要把功能 4 视为已验收，也不要在通过前继续查询或绘图。
本项目采用“自动测试绿灯 + 用户亲自在浏览器验收”逐项推进；代码保持简洁，不添加无必要的业务实体、框架层或数据库表。

## 当前进度

| 功能 | 实现状态 | 验收状态 |
| --- | --- | --- |
| 1. 已结束任务筛选 | 完成 | 用户明确通过 |
| 2. 字段与关联配置 | 完成，含单页简化修订 | 用户明确通过 |
| 3. 宽表表头 | 完成 | 用户明确通过 |
| 4. 提示词生成 SQL | 完成，接入真实本地 Ollama | 自动测试通过，用户人工验收待进行 |
| 5. 实际查询及图表 | 未实施 | 等功能 4 通过后实施 |

最近一次自动测试：88 passed；JS 语法与 DOM 引用检查通过。测试客户端有一条已知 Starlette 依赖弃用提示。
已完成真实模型单任务、多任务 SQL、缺关联澄清测试，以及浏览器交互自检。
完整 SQL 只做过 MySQL EXPLAIN 计划验证，没有执行分析取数，没有伪造数据结果或图表。

## 已确认的产品规则

- 当前是单页左右工作区：左侧选择任务，右侧“任务关联 / 宽表表头 / 智能分析”。底部只有一个随页签切换的主按钮。
- 任务仅限 task_status=COMPLETED 且 delete_flag=0；取数时先按未删除填报记录读取，不限制 fill_status/audit_status。
- 字段来自填报记录上的历史模板，不使用任务 current_template_no 替代。保留历史名称及同名不同编码。
- 多组两两任务关系，每组多条条件按 AND；INNER/LEFT JOIN，LEFT 保留左侧；不自动推荐关联字段，不要求全部任务连通。
- 对齐按字符串完全相等，保留大小写和空格，缺失/null 不匹配，空字符串可匹配。
- 关系是否满足具体需求在分析阶段判断。模型不可编造关系；有歧义或缺关系时澄清。
- NUMBER 才可 sum/avg/count；非法数字忽略，不当作 0；count 统计有效数字。类型冲突字段不直接作为数值度量。
- 已取消“手工填写分析 JSON”路线。新路线：用户提示词 + 表头与关联元信息 → 模型生成分析 SQL → 服务端校验 → 后续实际查询绘图。
- 模型暂用本地 Ollama，后续更换公司模型。只读账号、元信息白名单、SQL AST 校验均已实现；实际执行入口尚未开放。
- 只展示一张图，后续支持柱/线/饼，可更换类型与数据源，并对外提供接口。
- 不做登录鉴权（后期实现）、导出、写库、物理宽表、关联推荐、衍生公式重算、预警和异常台账。

## 实现位置

- app/main.py：FastAPI 页面与接口，当前版本 0.4.0。
- app/db.py：MySQL/TiDB 配置、只读连接。
- app/relations.py：任务资格、历史字段、关联配置验证。
- app/functions.py：build_wide_table_schema、逻辑 SQL 字段目录、限定范围 CTE SQL 生成。
- app/llm.py：Ollama transport call_model、结构化返回、SQL AST 校验、最多一次模型纠正。
- static/：原生 HTML/CSS/JavaScript，无前端构建框架。
- scripts/local_db.py：独立本地 MySQL 初始化/启动/停止、建筑业务测试元信息。
- scripts/start.ps1：启动网页服务。
- tests/：88 项自动测试；运行前强制检查为本机测试数据库。
- docs/acceptance-01.md 至 acceptance-04.md：逐项验收与测试记录。
- docs/*-preview.png：各阶段界面截图。
- docs/source-ddl.sql、requirements-original.md：用户提供的原始输入，仅为历史基线；需求变更以本文件和 README 为准。

## 环境与重建

开发机：Windows PowerShell，Python 3.12.8、Node.js 22.14.0。
数据库：项目内便携式 MySQL 8.4.13，仅监听 127.0.0.1:13306；数据库 report_assistant_demo，应用只读账号 report_reader。
模型：本机 Ollama http://127.0.0.1:11434，qwen3.6-35b:latest（约 23.4 GB，已在原机器安装）。
网页：http://127.0.0.1:8000/；API 文档：http://127.0.0.1:8000/docs。

全新克隆：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/local_db.py setup
.\scripts\start.ps1
```

setup 自动下载校验官方 MySQL ZIP，创建隔离测试实例、测试表和元信息、随机本地密码与 .env。
当前测试数据包含 16 个已结束的产值/成本/材料/劳务任务，另有状态排除样例和历史模板样例；尚未填充用于实际查询的行 JSON。
原机器再次启动只需先运行 local_db.py start，再运行 start.ps1；不要同时启动多个 8000 端口服务。
Ollama 需单独运行并具有上述模型。LLM_* 参数见 .env.example，模型协议更换只改 app/llm.py 中 call_model。
生产库连接只改 .env 的 DB_*、APP_ENV 与 TLS 配置，重启应用；真实 TiDB 尚未连通验证，严禁对生产执行测试初始化或造数。

验证：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check static/app.js
```

## 明天的下一步

1. 在浏览器验收功能 4：选择“项目成本月报 · 10月”→ 生成表头 → 智能分析，输入“按项目名称汇总项目总成本，使用柱状图。”。
2. 检查 SQL、字段映射、模型名、提示词和任务变化后的旧结果清除；缺关联时应澄清或被服务器拦截。
3. 等用户明确回复“功能 4 通过”。
4. 再实现实际查询与图表：补充有建筑业务含义且预期结果可人工计算的行 JSON 测试数据，覆盖四分表、精确字符串关联、LEFT 未匹配、重复键组合、非法数字、空值、筛选与聚合。
5. 执行入口必须重新构建服务端表头/限定范围 SQL 并校验，不能信任客户端传回的 prepared_sql。验证分组对大小写、尾空格的处理，限定资源与结果量，明确提示截断。
6. 接入 Chart.js，支持一维多度量柱/线图、一维一度量饼图，并提供对外查询/看板接口。每个后续功能仍先自动绿灯再用户验收。

## 备份边界

仓库包含完整源码、原始需求与 DDL、进度、测试、截图、配置样例和环境重建脚本。
真实 .env、本地数据库文件、账号密码、模型权重、虚拟环境、临时日志与缓存不提交；这些内容由 .gitignore 排除，避免上传密钥和不可移植的大文件。
原机器仍保留这些运行文件。其它机器可根据脚本和配置样例重建；不需要把 .runtime 或 .venv 上传 GitHub。
