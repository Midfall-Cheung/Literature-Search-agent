# P1-01：Streamlit 前端基础框架与阶段 A 可用页面

> 项目：Literature Search Agent（当前后端 v0.5.0 + 已完成 P0 数据校验）  
> 优先级：P1 / 子任务 01  
> 执行者：Codex  
> 保存位置：`docs/tasks/P1-01-frontend-foundation-phase-a.md`  
> 本次目标：**只把阶段 A 接入可用的 Streamlit 界面**，不开发 B～E 页面。

## 0. 执行与范围

遵循项目根目录 `AGENTS.md`。本次**直接实现代码和测试**，不要只出方案。开始前运行 `git status --short`，保留并尊重用户未提交的改动。只阅读与本任务直接相关的文件，优先：

- `app/api/routes.py`
- `app/schemas/question.py`
- `app/services/clarifier.py`、`app/services/projects.py`（遇到状态问题再阅读）
- `tests/unit/test_api.py`
- `pyproject.toml`、`README.md`
- `frontend/`（检查现有内容，优先复用）

**禁止范围**：不重构 LangGraph，不变更数据库模式，不重做阶段 A 解析器，不修改 B～E 业务逻辑，不接入新的 LLM，不开发多页面框架/复杂权限系统，不引入 React/Vue，不开启外部真实检索或下载，不清空 `workspace/`。若确需后端修复，先核实可通过前端规避；仅在必要时进行最小变更并解释。

## 1. 交付文件（可依现有结构微调）

- `frontend/app.py`：Streamlit 入口及阶段 A 页面。
- `frontend/api_client.py`：调用 FastAPI 的轻量客户端，避免 UI 内到处散落 HTTP 请求。
- `frontend/__init__.py`：如需让模块导入更稳定，可创建。
- `tests/unit/test_frontend_api_client.py`：使用 HTTP mock 的客户端测试。
- `tests/unit/test_frontend_app.py` 或现有同类文件：对关键 UI 状态进行可自动化的测试；若受环境限制无法跑 Streamlit UI 测试，至少保证 API 客户端覆盖并说明原因。
- `pyproject.toml`：增加前端依赖，优先使用独立可选依赖组 `ui`（例如 `streamlit>=1.40,<2`），不破坏后端安装。
- `README.md`：补充阶段 A 前端安装/启动说明，注明 B～E 暂未实现 UI。

不要为满足目录建议而创建大量空文件。

## 2. 必须使用的现有后端接口

只使用这些真实路由（不要凭想象新增端点）：

| 操作 | HTTP | 端点 | 请求体 |
|---|---|---|---|
| 健康检查 | GET | `/health` | 无 |
| 创建项目 | POST | `/projects` | `{"original_question":"..."}` |
| 恢复/刷新项目 | GET | `/projects/{project_id}/state` | 无 |
| 回答当前问题 | POST | `/projects/{project_id}/messages` | `{"content":"..."}`；仅必要时附 `field_updates` |
| 确认阶段 A | POST | `/projects/{project_id}/confirm-question` | `{"accepted":true}` |
| 修订阶段 A | POST | `/projects/{project_id}/confirm-question` | `{"accepted":false,"feedback":"...","field_updates":{"outcomes":["..."]}}` |

**状态与响应结构以本地源码/OpenAPI 实际定义为准**，主要字段：

- `project_id`
- `status`：`clarifying` / `awaiting_confirmation` / `confirmed`
- `current_question`：含 `prompt`、`target_fields`、`round_number`，可能是 `null`
- `question_spec`：含 `framework`、`population_or_object`、`intervention_or_exposure`、`outcomes`、`study_types`、`date_from`、`date_to`、`languages`、`explicitly_unrestricted` 等
- `messages`：已有对话记录
- `summary`、`assumptions`、`missing_fields`、`clarification_round`、`max_rounds`

`ConfirmQuestionRequest` 在 `accepted=false` 时**必须包含非空 `field_updates`**；仅发送 `feedback` 不能完成修订。字段修订必须尊重最新 P0 的类型校验与“不限”语义。

## 3. FastAPI HTTP 客户端

在 `frontend/api_client.py` 封装可测试的客户端：

1. 基础 URL 从环境变量 `LITERATURE_API_BASE_URL` 读取，默认 `http://127.0.0.1:8000`；不要硬编码为 Docker 内部域名。
2. 提供 `health()`、`create_project(question)`、`get_state(project_id)`、`answer(project_id, content, field_updates=None)`、`confirm(project_id)`、`revise(project_id, field_updates, feedback=None)` 等方法。
3. 使用 `httpx`（项目已有依赖），设置合理超时；将 HTTP 400/404/409/422/5xx 与连接错误转换为可供 UI 展示的清晰异常，而不是暴露堆栈给用户。
4. 保留服务端 `detail` 中的有效中文提示；Pydantic 422 可能是列表结构，需提取可读信息。
5. 不在日志里输出 API 密钥、研究问题全文或带敏感信息的查询参数。
6. API 客户端不得访问数据库、直接调用 LangGraph 或改写服务端状态。
7. 设计必要的依赖注入以便用 `httpx.MockTransport`、mock 客户端或 Streamlit 测试工具验证。

## 4. 阶段 A 页面功能与交互

### 4.1 新建与恢复

- 顶部显示“文献检索 Agent · 阶段 A：研究问题澄清”。
- 输入原始研究问题并点击“创建项目”，成功后显示新 `project_id` 并读取当前状态。
- 独立提供“输入已有项目 ID → 加载项目”的入口，不应因已有会话而隐藏。
- 将当前 `project_id` 保存在 `st.session_state`；每次关键动作以后使用后端响应更新 state，必要时可手动刷新。
- 浏览器刷新后至少可通过重新输入 `project_id` 恢复；**不得声称刷新后无需输入 ID 就一定自动恢复**（除非确实实现并测试了稳定持久化方式）。
- 服务端不可达时给出友好提示与启动方法。

### 4.2 澄清问答（`clarifying`）

- 按消息角色显示 `messages`，不要把原始问题在 UI 里重复显示两次。
- 清晰展示当前 `current_question.prompt`、轮次（例如 `2/6`）和 `target_fields` 对应的人类可读标签。
- 用户提交回答时请求 `/messages`，成功后更新 UI 并显示新问题。
- 对日期和语言输入保留自然语言回答方式，由后端 P0 做解析/校验；**不要在前端通过逗号切分生成语言数组**。
- 提交时防止双击重复提交（至少按钮禁用/会话动作保护），网络失败后可以重试。
- 错误回答时保留输入值和当前页面/问题，显示后端校验提示，不自行推进轮次。
- 在可读区域展示实时 `question_spec`、缺失字段、当前系统假设（可以 `st.expander`）。

### 4.3 确认与修订（`awaiting_confirmation`）

- 展示 `summary` 和所有 `assumptions`，提示必须明确确认才能完成阶段 A。
- “确认研究问题”发送 `{"accepted":true}`，成功后显示“阶段 A 已确认”。
- 提供**真实可用的修订入口**，不能只有一个未连后端的编辑表单。
- 建议以单字段修订为第一版：选择字段 → 根据字段类型输入 → 生成符合 API 的 `field_updates` → 提交 `accepted=false`。
- 至少覆盖：`population_or_object`、`intervention_or_exposure`、`outcomes`、`study_types`、`date_from`、`date_to`、`languages`。
- 列表类字段提交列表而非逗号连接字符串；语言字段用 `zh/en/...` 合法代码的多选组件或按后端允许代码配置；年份字段提交整数或 `null`，区分 `None` 与“不限”。
- 如提供“不限”控制，必须按照现有 `explicitly_unrestricted` 协议处理，保证不产生“具体值 + 不限”的冲突；不确定是否支持的操作应禁用并标注，而不是发出无效 payload。
- 修订成功后重新读取服务端状态，显示修订结果和后端当前状态；可能重新进入 `awaiting_confirmation`，不要假设必定返回 `clarifying`。
- 确认前不允许启动阶段 B 的接口。

### 4.4 已确认（`confirmed`）

- 只读展示最终确认的结构化问题和摘要。
- 明确告知“阶段 A 已完成；阶段 B 的前端页面将在 P1-02 实现”。
- **本任务不需要做跳转到假页面的按钮**。

## 5. 用户体验与错误处理

- 页面文字默认中文，布局简洁，手机宽度下不出现严重溢出；优先使用 Streamlit 原生组件。
- 对项目不存在、服务不可达、回答格式错误、错误状态跳转、重复点击、请求超时等情况给出清楚提示。
- 不打印原始异常堆栈给终端用户；开发调试可记录经过脱敏的异常类型。
- 网络通信失败时不得清空已保存的 `project_id` 或丢失用户待提交的输入。
- Streamlit 的 rerun 不应意外发起创建项目、回答、确认等写操作。

## 6. 测试与验收

### 6.1 必须添加的自动化测试

使用 fake/mock HTTP 客户端，不依赖真实学术 API：

1. 创建项目请求体正确、响应状态被解析。
2. 加载已有项目、404、API 不可达时正确处理。
3. 澄清答复携带 `content`，不错误地将年份拆进语言字段。
4. 接口返回 409/422 时 UI/客户端可呈现有效提示，不静默覆盖状态。
5. `accepted=true` 与修订 `accepted=false + field_updates` 的请求形状正确。
6. 语言修订提交合法数组，年份修订提交合法整数/空值。
7. 若添加 Streamlit UI 自动化测试，验证失败提交不触发下一次写操作，按钮在正确状态出现。

### 6.2 本地人工验收脚本（需要实际操作，不仅仅声称）

1. 后端启动；浏览器访问 Streamlit 前端。
2. 创建问题“**大语言模型能否改善高校教师的文献检索效率？**”。
3. 按服务端实际提问完成多轮回答；语言示例“我想检索中文和英文文献”，时间示例“2020—2026”。
4. 触发一次错误语言输入 `[2025,2026]`，检查前端提示且轮次不前进；再正确提交，确认可继续。
5. 进入确认态后把 `outcomes` 修改为 `["检索效率", "检索准确率"]` 并提交，检查服务端状态已修改。
6. 确认问题，页面出现 `confirmed`。
7. 使用同一 `project_id` 从“加载项目”入口恢复，看到与后端相符的状态。

如果人工浏览器操作受当前 Codex 环境限制，必须明确标为“未人工验证”，不要虚构实际点击结果；可执行非浏览器 smoke test 代替。

### 6.3 测试命令（按实际环境执行）

```bash
.venv/bin/python -m pytest tests/unit/test_frontend_api_client.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q frontend

git diff --check
```

如依赖尚未安装，应使用项目现有 Python 环境安装前端可选依赖（优先 `pip install -e '.[dev,ui]'`），不要随意重建虚拟环境。

## 7. 交付文档与启动命令

在 README 写明（根据实际验证结果调整）：

```bash
# 终端 1：启动 FastAPI
.venv/bin/literature-clarifier --reload

# 终端 2：启动 Streamlit
.venv/bin/streamlit run frontend/app.py
```

前端默认地址 `http://127.0.0.1:8501`，后端默认地址 `http://127.0.0.1:8000`。如环境要求自定义后端地址，可配置 `LITERATURE_API_BASE_URL`。

## 8. 完成标准与最终输出

**只有满足以下条件才可标记 P1-01 完成：**

- 新建项目、恢复项目、澄清问答、展示结构化状态、修订和最终确认可由 Streamlit 完成。
- 后端仍是唯一事实来源；没有复制 A 阶段业务逻辑到前端。
- 没有触碰 B～E 功能代码；不存在无效假按钮。
- 有新增测试并执行，报告真实通过/失败数。
- 后端现有测试没有因前端加入而退化。
- README 有真实可用的双终端启动指令。

任务完成后只需汇报：

1. 完成内容（简短）
2. 修改文件列表
3. 测试命令和结果（通过/失败/跳过）
4. 是否完成真实浏览器人工验收（如否说明原因）
5. 剩余问题

**完成 P1-01 后停止；不要自动进入 P1-02（阶段 B 词表页面）。**
