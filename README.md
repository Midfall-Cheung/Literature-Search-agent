# Literature Search Agent：阶段 A-E

这是《文献检索 Agent 架构与实施方案》中阶段 A（澄清研究问题）、阶段 B（生成可审计检索词表）、阶段 C（编译多数据源查询计划）、阶段 D（多数据源首轮检索）和阶段 E（合法开放全文解析与统一下载）的可运行原型。

## 已实现

- PICO、PECO、SPIDER、Concept–Context–Outcome 四种问题框架识别；
- 按“对象 → 核心概念 → 结果 → 文献类型 → 时间 → 语言”的优先级提问；
- 每轮只问一个高价值问题，时间上下界作为一组，最多 6 轮；
- LangGraph `interrupt`/`Command(resume=...)` 人在回路工作流及 SQLite checkpoint；
- FastAPI 创建项目、回答问题、读取状态和确认/修订接口；
- SQLite 中的项目、消息、问题版本和审计事件持久化；
- “不限”条件的显式记录，不把缺失值误当成不限；
- 达到轮数上限后的可见假设、问题摘要和显式确认；
- 默认的确定性结构解析器、通用 `StructuredLLMQuestionAnalyzer`，以及可注入的 `QuestionAnalyzer` 接口；
- 按概念块生成中英文首选词、同义词、缩写和翻译，所有词保留来源；
- 词表启用/停用、编辑、重新生成、乐观锁和完整版本快照；
- 概念内 `OR`、概念间 `AND` 的 broad/focused/exact phrase 查询预览；
- UTF-8 BOM `terms.csv` 导出，便于 Excel 直接打开中文内容；
- 为 OpenAlex、Crossref、Semantic Scholar 编译可审计的查询参数；
- 查询计划草稿、确认、显式覆盖、乐观锁和版本快照；
- OpenAlex、Crossref、Semantic Scholar 分页检索及按数据源隔离失败；
- 每页持久化原始记录、游标和响应哈希，失败运行可以续跑；
- DOI、外部标识符、规范化标题与年份的分层去重；
- 统一文献模型、来源/查询溯源、透明词项相关性评分和 CSV 导出；
- 元数据、Unpaywall、Europe PMC、arXiv、CORE 顺序全文解析；
- `selected`、`top_n_oa`、`all_oa` 三种下载策略及批量确认门禁；
- HTTPS/重定向限制、Content-Type 与 `%PDF-` 双重校验、大小限制；
- `.part` 原子写入、SHA-256 去重、跨平台安全文件名和 URL token 脱敏；
- 下载断点续跑、JSONL manifest、失败 CSV 和人工获取状态；
- 阶段 A-E 的 API、单元测试和断点恢复测试。

## 快速开始

需要 Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/literature-clarifier --reload
```

打开 <http://127.0.0.1:8000/docs> 使用交互式 API 文档。

## Streamlit 阶段 A / B 前端

在现有 Python 3.12 虚拟环境中安装可选 UI 依赖，不需重建环境：

```bash
.venv/bin/python -m pip install -e '.[dev,ui]'

# 终端 1：启动 FastAPI
.venv/bin/literature-clarifier --reload

# 终端 2：启动 Streamlit
.venv/bin/streamlit run frontend/app.py
```

后端默认 <http://127.0.0.1:8000>，前端默认 <http://127.0.0.1:8501>。
如需连接其他后端，在前端终端配置：

```bash
LITERATURE_API_BASE_URL=http://127.0.0.1:8000 .venv/bin/streamlit run frontend/app.py
```

页面支持新建项目、按项目 ID 加载/刷新、多轮自然语言澄清、查看结构化状态和系统假设、单字段修订及明确确认。请保存项目 ID；浏览器刷新后可重新输入 ID 恢复。语言回答直接发送后端解析，例如“我想检索中文和英文文献”，日期可填写“2020—2026”。错误输入会保留草稿并显示后端提示。

确认前可修订研究对象、核心概念、结果、研究类型、年份及语言。结果/研究类型每行一项；语言使用合法代码多选；年份区分整数、尚未指定的空值和明确“不限”。修订后先检查服务端摘要，再点击“确认研究问题”。请求超时可能已保存，先刷新项目核对后再重试。

**目前提供阶段 A 与阶段 B 页面；C～E 尚未实现 UI，本次不执行查询计划、真实检索或下载。**

### 阶段 B：检索词表

加载或创建项目后，通过“工作区”选择“阶段 B”。必须先明确确认阶段 A，才能生成、编辑或确认词表。首次进入只读取状态；点击“生成检索词表”才实际生成。

- 按概念块查看已保存术语及来源、规范词、时间等只读信息；使用“选择术语”逐项编辑，也可增加或移除行。已有词的 ID 和来源保持不变，新词来源为 `user`。
- 编辑只修改本地草稿；“保存完整词表”发送全部行和草稿基准版本。成功后采用服务器版本。草稿按项目隔离，同一会话的 rerun、页面切换或刷新服务器词表不会覆盖未保存修改。
- 遇到 409，先“刷新服务器词表（保留草稿）”并展开服务器词表与本地草稿比较。可以勾选后放弃草稿；也可明确授权采用最新版本号再保存完整草稿。后者会覆盖其他编辑，旧 ID 若已失效，仍由后端拒绝，不会自动伪造或转换 ID。
- “加载查询预览”显示后端返回的查询及词表版本；未保存修改不参与预览。“加载版本历史”只读，不会回滚当前词表。“获取 CSV 导出”后点击“下载 terms.csv”，保留 UTF-8 BOM。
- 没有未保存修改、且至少两个概念块有启用的非排除术语时，才能明确“确认检索词表”。确认后显示只读快照；重编辑须勾选许可，保存可能使确认失效。
- 重新生成须勾选覆盖确认，再按“重新生成并覆盖词表”。这会覆盖服务器词表及当前草稿，不会在刷新或页面载入时自动执行。

未保存草稿只存于当前 Streamlit 会话；浏览器刷新、服务重启或新会话可能丢失，请先保存。后端已保存词表可通过项目 ID 重新加载。请求超时后应先刷新比较，避免重复提交。

前端回归测试和编译检查：

```bash
# 隔离 app.main 导入时初始化的默认应用，不使用真实 workspace 数据库
p1_test_root=$(mktemp -d /tmp/literature-p1-tests.XXXXXX)
export LITERATURE_DATABASE_URL="sqlite:///$p1_test_root/import.db"
export LITERATURE_CHECKPOINT_DB="$p1_test_root/checkpoints.db"
export LITERATURE_PROJECTS_ROOT="$p1_test_root/projects"
.venv/bin/python -m pytest tests/unit/test_frontend_api_client.py tests/unit/test_frontend_app.py tests/unit/test_frontend_terms.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q frontend
```

UI 自动化使用 [Streamlit AppTest](https://docs.streamlit.io/develop/api-reference/app-testing)，后端安装未包含 `ui` 时会跳过 UI 测试，HTTP 客户端测试仍可运行。AppTest 不等同于真实浏览器人工验收；本次开发环境无可用浏览器，已通过临时后端的真实 HTTP + AppTest 完成创建、按当前目标回答六轮、错误语言输入重试、修订结果、确认和新 UI 会话按 ID 恢复，并验证双方服务器健康检查。真实浏览器人工验收尚未执行。

## API 示例

创建项目：

```bash
curl -s http://127.0.0.1:8000/projects \
  -H 'Content-Type: application/json' \
  -d '{"original_question":"大语言模型能否改善高校教师的文献检索效率？"}'
```

使用上一步返回的 `project_id` 回答当前问题：

```bash
curl -s http://127.0.0.1:8000/projects/PROJECT_ID/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"高校教师"}'
```

当 `status` 变为 `awaiting_confirmation` 后确认：

```bash
curl -s http://127.0.0.1:8000/projects/PROJECT_ID/confirm-question \
  -H 'Content-Type: application/json' \
  -d '{"accepted":true}'
```

如需修订，必须传明确的字段更新，避免自由文本被错误覆盖：

```json
{
  "accepted": false,
  "feedback": "研究对象改为大学生",
  "field_updates": {"population_or_object": "大学生"}
}
```

## 阶段 B API

只有阶段 A 的 `question_spec.confirmed=true` 后才能生成词表：

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/terms/generate \
  -H 'Content-Type: application/json' \
  -d '{"replace_existing":false}'
```

词表接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/projects/{id}/terms/generate` | 首次生成或明确覆盖词表 |
| `GET` | `/projects/{id}/terms` | 获取按概念分组的当前词表 |
| `PUT` | `/projects/{id}/terms` | 用 `expected_version` 编辑整张词表 |
| `GET` | `/projects/{id}/terms/history` | 查看草稿和确认版本快照 |
| `POST` | `/projects/{id}/confirm-terms` | 确认至少含两个有效概念块的词表 |
| `GET` | `/projects/{id}/query-preview` | 查看规范化查询变体 |
| `GET` | `/projects/{id}/terms/export.csv` | 下载 `terms.csv` |

`PUT` 使用乐观锁：客户端提交当前 `version` 作为 `expected_version`。修改已确认词表会生成新草稿版本，不会覆盖历史版本。

## 阶段 C API

只有阶段 B 的词表 `status=confirmed` 后才能编译查询计划：

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/query-plans/compile \
  -H 'Content-Type: application/json' \
  -d '{"providers":["openalex","crossref","semantic_scholar"],"page_size":100,"max_records":500,"replace_existing":false}'
```

查询计划接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/projects/{id}/query-plans/compile` | 为选定数据源编译查询计划 |
| `GET` | `/projects/{id}/query-plans` | 查看当前查询计划和实际请求参数 |
| `GET` | `/projects/{id}/query-plans/history` | 查看草稿和确认版本快照 |
| `POST` | `/projects/{id}/confirm-query-plan` | 用 `expected_version` 确认计划 |

OpenAlex 保留标准布尔查询；Semantic Scholar 转换为 bulk search 的 `+`、`|`、`-` 语法；Crossref 的 `query.bibliographic` 不保证布尔语义，因此降级为关键词相关性查询，并在 `notes` 中明确记录限制。阶段 C 只编译和审计请求，不会向这些外部服务发送请求。

## 阶段 D API

只有阶段 C 查询计划 `status=confirmed` 且仍对应当前已确认词表时，才能启动检索。默认只执行每个来源的 `broad` 查询：

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/search-runs \
  -H 'Content-Type: application/json' \
  -d '{"purposes":["broad"],"max_records_per_query":100}'
```

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/projects/{id}/search-runs` | 创建并执行一次检索运行 |
| `GET` | `/projects/{id}/search-runs/{run_id}` | 查看运行、来源和查询统计 |
| `POST` | `/projects/{id}/search-runs/{run_id}/resume` | 从已保存游标续跑失败或中断任务 |
| `GET` | `/projects/{id}/works` | 分页查看去重后的文献，可按来源/OA 筛选 |
| `GET` | `/projects/{id}/exports/works.csv` | 导出 UTF-8 BOM 文献结果表 |

也可以在请求中用 `providers` 选择数据源，或用 `query_ids` 精确选择阶段 C 的查询。`query_ids` 与 `providers` 不能同时提交。运行状态含义：`completed` 全部完成、`partial` 部分来源失败、`failed` 全部失败。

阶段 D 不下载全文。`pdf_url` 只是数据源提供的候选开放地址，验证许可并下载 PDF 属于阶段 E。

## 阶段 E API

建议先用 `selected` 对一篇明确开放的文献做冒烟测试：

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/downloads/estimate \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["WORK_ID"]}'

curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/downloads \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["WORK_ID"]}'
```

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/projects/{id}/downloads/estimate` | 估算文件数和磁盘占用 |
| `POST` | `/projects/{id}/downloads` | 创建并执行下载运行 |
| `GET` | `/projects/{id}/downloads/{run_id}` | 查看逐篇状态和解析尝试 |
| `POST` | `/projects/{id}/downloads/{run_id}/resume` | 重试失败或未完成项目 |
| `GET` | `/projects/{id}/downloads/{run_id}/manifest.jsonl` | 下载成功/失败审计清单 |
| `GET` | `/projects/{id}/downloads/{run_id}/failed.csv` | 下载人工获取与失败报告 |

`all_oa` 必须先查看估算，再在执行请求中明确传入 `"confirm_all_oa": true`。所有成功 PDF 位于 `workspace/projects/{project_id}/papers/`；阶段 E 新增的下载记录、manifest 和 API 响应只保留去除查询参数后的 URL。

完整检测步骤见 [阶段 E 检测指南](docs/阶段E检测指南.md)。

## 目录

```text
app/
├── api/routes.py              # 阶段 A HTTP 接口
├── api/terms.py               # 阶段 B HTTP 接口
├── api/query_plans.py         # 阶段 C HTTP 接口
├── api/retrieval.py           # 阶段 D HTTP 接口
├── api/downloads.py           # 阶段 E HTTP 接口
├── providers/compilers.py     # 数据源查询编译器
├── providers/retrievers.py    # 分页检索和来源记录规范化
├── providers/http.py          # 超时、429/5xx 重试和安全 HTTP 客户端
├── resolvers/fulltext.py      # 合法开放全文地址解析链
├── graph/workflow.py          # interrupt/resume 状态图
├── repositories/projects.py   # SQLite/SQLAlchemy 持久化与版本审计
├── repositories/terms.py      # 当前词表与不可变版本快照
├── repositories/query_plans.py # 当前查询计划与不可变版本快照
├── repositories/retrieval.py # 运行、原始记录、文献与命中来源
├── repositories/downloads.py # 下载运行、尝试、文件和 SHA-256 关联
├── schemas/question.py        # ResearchQuestionSpec 与 API 模型
├── schemas/terms.py           # 词、概念、查询预览模型
├── schemas/query_plans.py     # 数据源查询与计划模型
├── schemas/retrieval.py       # 检索运行、规范化文献和分页模型
├── schemas/downloads.py       # 下载策略、运行、尝试和文件模型
├── services/clarifier.py      # 框架、缺口、提问、合并和摘要规则
├── services/term_builder.py   # 词表生成、规范化与查询组合
├── services/terms.py          # 阶段 B 业务规则与 CSV 导出
├── services/query_plans.py    # 阶段 C 门禁、编译和确认规则
├── services/retrieval.py      # 阶段 D 执行、评分、查询选择和导出
├── services/downloads.py      # 阶段 E 编排、去重、manifest 和恢复
├── services/pdf_downloader.py # 安全流式下载与 PDF 校验
└── main.py                    # 应用装配
```

## 模型接入点

离线默认使用 `HeuristicQuestionAnalyzer`，只抽取高置信度标签和边界，便于无密钥启动和稳定测试。生产环境可把任意实现 `with_structured_output()` 的聊天模型传给 `StructuredLLMQuestionAnalyzer`，再通过 `create_app(..., analyzer=...)` 注入；也可以自行实现 `QuestionAnalyzer.analyze()`。缺口计算、追问顺序、轮数上限、暂停恢复和确认仍由确定性代码控制。

阶段 B 默认用透明的内置小词表扩展常见中英文术语。生产环境可注入 `StructuredLLMTermExpander`，或用 `CompositeTermExpander` 同时使用规则与模型。模型产生的词会被代码强制标记为 `source=llm`，且不能冒充 MeSH 等受控词表术语。

环境变量见 `.env.example`。阶段 E 建议配置 `UNPAYWALL_EMAIL`，CORE 增强解析需要 `CORE_API_KEY`；arXiv、Europe PMC 和已确认 OA 的元数据地址不需要密钥。密钥只在请求时注入，不写入查询计划、数据库或错误信息。默认运行数据写入 `workspace/`。
