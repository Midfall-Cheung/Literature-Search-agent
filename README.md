# Literature Search Agent：阶段 A

这是《文献检索 Agent 架构与实施方案》中“阶段 A：通过多轮提问澄清研究问题”的可运行原型。它不会执行文献检索；当前交付边界止于用户显式确认 `ResearchQuestionSpec`。

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
- 单元测试与完整六轮 API 流程测试。

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

## 目录

```text
app/
├── api/routes.py              # 阶段 A HTTP 接口
├── graph/workflow.py          # interrupt/resume 状态图
├── repositories/projects.py   # SQLite/SQLAlchemy 持久化与版本审计
├── schemas/question.py        # ResearchQuestionSpec 与 API 模型
├── services/clarifier.py      # 框架、缺口、提问、合并和摘要规则
└── main.py                    # 应用装配
```

## 模型接入点

离线默认使用 `HeuristicQuestionAnalyzer`，只抽取高置信度标签和边界，便于无密钥启动和稳定测试。生产环境可把任意实现 `with_structured_output()` 的聊天模型传给 `StructuredLLMQuestionAnalyzer`，再通过 `create_app(..., analyzer=...)` 注入；也可以自行实现 `QuestionAnalyzer.analyze()`。缺口计算、追问顺序、轮数上限、暂停恢复和确认仍由确定性代码控制。

环境变量见 `.env.example`。默认运行数据写入 `workspace/`，不会写入 API key 或全文内容。
