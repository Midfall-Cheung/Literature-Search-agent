# Literature Search Agent：阶段 A + B + C

这是《文献检索 Agent 架构与实施方案》中阶段 A（澄清研究问题）、阶段 B（生成可审计检索词表）和阶段 C（编译多数据源查询计划）的可运行原型。它尚不调用外部学术数据库；当前交付边界止于用户确认查询计划。

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
- 阶段 A、B、C 的 API、单元测试和断点恢复测试。

## 快速开始

需要 Python 3.12：

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/literature-clarifier --reload
```

打开 <http://127.0.0.1:8000/docs> 使用交互式 API 文档。

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

## 目录

```text
app/
├── api/routes.py              # 阶段 A HTTP 接口
├── api/terms.py               # 阶段 B HTTP 接口
├── api/query_plans.py         # 阶段 C HTTP 接口
├── providers/compilers.py     # 数据源查询编译器
├── graph/workflow.py          # interrupt/resume 状态图
├── repositories/projects.py   # SQLite/SQLAlchemy 持久化与版本审计
├── repositories/terms.py      # 当前词表与不可变版本快照
├── repositories/query_plans.py # 当前查询计划与不可变版本快照
├── schemas/question.py        # ResearchQuestionSpec 与 API 模型
├── schemas/terms.py           # 词、概念、查询预览模型
├── schemas/query_plans.py     # 数据源查询与计划模型
├── services/clarifier.py      # 框架、缺口、提问、合并和摘要规则
├── services/term_builder.py   # 词表生成、规范化与查询组合
├── services/terms.py          # 阶段 B 业务规则与 CSV 导出
├── services/query_plans.py    # 阶段 C 门禁、编译和确认规则
└── main.py                    # 应用装配
```

## 模型接入点

离线默认使用 `HeuristicQuestionAnalyzer`，只抽取高置信度标签和边界，便于无密钥启动和稳定测试。生产环境可把任意实现 `with_structured_output()` 的聊天模型传给 `StructuredLLMQuestionAnalyzer`，再通过 `create_app(..., analyzer=...)` 注入；也可以自行实现 `QuestionAnalyzer.analyze()`。缺口计算、追问顺序、轮数上限、暂停恢复和确认仍由确定性代码控制。

阶段 B 默认用透明的内置小词表扩展常见中英文术语。生产环境可注入 `StructuredLLMTermExpander`，或用 `CompositeTermExpander` 同时使用规则与模型。模型产生的词会被代码强制标记为 `source=llm`，且不能冒充 MeSH 等受控词表术语。

环境变量见 `.env.example`。默认运行数据写入 `workspace/`，不会写入 API key 或全文内容。
