# P1-02｜Streamlit 阶段 B：检索词表可视化管理

> 项目：Literature Search Agent  
> 依赖：P0 数据校验已完成；P1-01 Streamlit 阶段 A 已实现  
> 优先级：P1-02（单一任务）  
> 交付方式：直接修改当前 VS Code 工作区代码，并提交简短中文开发报告  
> 约束：遵循仓库根目录 `AGENTS.md`；本任务完成即停止，不执行 P1-03～P1-05。

## 0. 目标与边界

基于现有 FastAPI 阶段 B API 和已实现的 Streamlit 阶段 A 页面，增加一个真正可用的「检索词表」工作区，使用户无需 Swagger/curl 就能：

1. 从**已确认的阶段 A 项目**生成词表；
2. 按概念块查看检索词（中英文、术语类型、来源、字段、启用状态）；
3. 修改、增加、移除检索词，保留现有词的 `term_id`；
4. 保存完整词表且尊重 `expected_version` 乐观锁；
5. 查看 broad/focused/exact_phrase 等后端返回的查询预览；
6. 查看版本历史并导出 CSV；
7. 显式确认词表，为后续阶段 C 做准备；
8. 重新进入该项目时从后端恢复已保存词表、版本和确认状态。

**仅做阶段 B 的前端接入与直接相关测试。** 不重新实现后端 TermBuilder，不改查询编译/检索/下载逻辑，不实现 C/D/E 页面，不添加新 Agent，不做数据库迁移。

## 1. 最先检查的文件（按需读取，避免全仓扫描）

优先阅读：

- `AGENTS.md`
- `frontend/api_client.py`（现有 API 客户端）
- `frontend/app.py`（现有阶段 A 页面、session_state 和回调机制）
- `app/api/terms.py`
- `app/schemas/terms.py`
- `app/services/terms.py`
- `tests/unit/test_frontend_api_client.py`
- `tests/unit/test_frontend_app.py`

需要确认数据流或失败路径时，再按需阅读：

- `app/repositories/terms.py`
- `app/services/term_builder.py`
- 阶段 B 现有测试文件

以**当前本地真实源码**为准，不要仅凭本任务文档推断接口。开始前运行当前测试，建立基线；不允许删除用户数据。

## 2. 后端 API 契约（须严格对照真实 Schema）

本任务只调用**已有端点**：

| 用户动作 | 方法及路径 | 重要约定 |
| --- | --- | --- |
| 查看词表 | `GET /projects/{project_id}/terms` | 若未生成，返回 `status=not_generated` / `version=0`；**GET 不自动生成** |
| 生成词表 | `POST /projects/{project_id}/terms/generate` | `{"replace_existing":false}`；只有用户按按钮时才调用 |
| 重新生成 | 同上 | `{"replace_existing":true}`；明确二次确认，有覆盖风险 |
| 保存编辑 | `PUT /projects/{project_id}/terms` | `{"expected_version":当前版本,"terms":[完整词表数组]}`；不是局部 PATCH |
| 查询预览 | `GET /projects/{project_id}/query-preview` | 展示后端提供的版本及 variants；不在前端自行编译 |
| 历史记录 | `GET /projects/{project_id}/terms/history` | 只读，显示版本、状态、时间及快照 |
| CSV 导出 | `GET /projects/{project_id}/terms/export.csv` | 二进制 CSV，保留 UTF-8 BOM，提供 `st.download_button` |
| 确认词表 | `POST /projects/{project_id}/confirm-terms` | `{"expected_version":当前版本}`；至少两块有启用的**非排除词**概念 |

不要错误地调用 `/exports/terms.csv`，不要传旧版 `{"version":...,"accepted":true}` 确认体，不要把 Swagger 的 `string` 示例存为真实术语。

关键 schema 事实（以本地源码核实）：

- `TermTable` 有 `project_id/status/version/concepts/terms/confirmed_at/...`；
- `TermInput` 可写字段：`term_id?`, `concept_id`, `concept_name`, `term`, `language`, `term_type`, `source`, `field_hint`, `enabled`, `notes`；
- `TermRecord` 返回的 `normalized_term/created_at/updated_at` 是只读显示字段，**不得写回** `TermInput`；
- 现有词须保留其 `term_id`，新增词省略 `term_id`（不要生成随机 ID，也不要复用别的项目 ID）；
- `TermLanguage` 为 `zh/en/other`；`TermType`、`TermSource`、`FieldHint` 枚举必须使用代码中的准确值；
- 后端会拒绝同概念块内规范化后重复的术语、错误来源组合及不属于当前项目的 term_id；
- 编辑保存后版本递增、确认状态可能重置为 `draft`。

## 3. P1-02A｜改造 APIClient 支持不同响应类型

**现存问题**：`frontend/api_client.py` 的 `_request()` 对所有非 health 响应都强制要求 `ProjectState` 特有字段（例如 `question_spec`、三种阶段 A 状态）。阶段 B 的 `TermTable/TermHistory/QueryPreview` 不是此结构，直接复用会误报响应格式异常。

要求：

1. 在保留现有阶段 A API 行为的前提下，把通用 HTTP 请求/错误解析与「各端点响应校验」分开；可使用清晰的 `response_kind`、独立私有方法或小型 validators，避免不必要的大规模重构。
2. 阶段 A 继续严格校验 `ProjectState`，`health` 仍只使用自己的结构校验；阶段 B 分别验证词表/历史/预览的最低必要字段，不能用阶段 A schema 校验阶段 B。
3. 处理 HTTP `409`（版本冲突）、`422`（校验失败）、`404`、超时、连接失败，展示后端安全、可读的信息；不显示密钥、请求原文或带 token 的 URL。
4. 增加明确的客户端方法，例如 `get_terms/generate_terms/replace_terms/get_query_preview/get_term_history/confirm_terms/export_terms_csv`。命名可随现有风格调整。
5. CSV 必须以字节读取，不走 JSON；保留响应中的合理 filename 或生成安全默认文件名。
6. 与现有 `LITERATURE_API_BASE_URL` 环境变量兼容；不直接读写 SQLite。
7. 不能让只读页面渲染、Streamlit rerun、切换 Tab、刷新状态自动触发 POST/PUT。

## 4. P1-02B｜界面与导航

保留 P1-01 阶段 A 已验证流程，在 `frontend/app.py` 内增加阶段 A/B 入口（可拆小组件文件，但不强制），默认中文：

### 4.1 前置条件与导航

- 在加载/新建项目后显示当前项目 ID、阶段 A 状态；
- 仅当阶段 A `status=confirmed` 且 `question_spec.confirmed=true` 时，允许**生成、编辑、确认**阶段 B 词表；
- 否则说明需先完成阶段 A；不要通过 UI 绕过服务器门禁；
- 阶段 A 页面保持可访问，不修改已有确认/修订规则；
- 词表已确认时显示明显的只读确认状态；若允许重编辑/重生成，必须警示「操作可能使确认失效」；
- 不用在本任务实现阶段 C 页面，最多展示「下一步：查询计划（P1-03）」的静态说明。

### 4.2 首次生成/查看

- 进入阶段 B 时先调用 `GET /terms`，展示 `not_generated` 空态；
- 用户点击「生成检索词表」后才 POST `replace_existing=false`；
- 生成期间禁用重复提交按钮，失败保留项目 ID 与原页面状态；
- 成功后展示词表版本、状态、概念块数量、检索词数量；
- 按 `concept_id/concept_name` 分组展示术语，支持查看 `normalized_term`、来源等只读信息。

### 4.3 编辑（最重要）

建议使用 `st.data_editor` 或分组表单，优先选择**易于保证类型和版本一致性**的交互方式，不强求复杂表格：

- 编辑 `term`, `concept_id`, `concept_name`, `language`, `term_type`, `field_hint`, `enabled`, `notes`；
- 已有行的 `source` 必须保留准确来源，不得在编辑后静默把 `heuristic/mesh/llm` 变成 `user`；如果允许用户修改来源，必须遵守真实溯源且给出明确说明；
- 新增术语默认 `source=user`，**不要把用户新加词冒充成 `mesh` 或 `llm`**；
- 保留现有行 `term_id`，删除行代表下一次**完整覆盖提交**时移除；
- 不允许向后端提交 `normalized_term/created_at/updated_at` 等只读字段；
- 编辑期间不因 rerun/页面切换/意外 GET 覆盖未保存的本地草稿；
- 空术语、错误枚举、同块重复词、0 行、概念 ID 与名字缺失等输入，在提交前给出易读提示（不要求复制后端全部规则，后端仍是最终校验者）；
- `PUT` 发送**完整当前草稿列表**，带从服务端取得的 `expected_version`；
- 保存成功后采用**服务器响应**更新版本及内容，清除成功提交的脏草稿；
- `409` 版本冲突时**绝不自动覆盖**；保留未保存草稿，提示「服务器已更新，请先刷新/比较」，由用户决定如何处理；
- 如果修改原词的 provenance 或 `term_id` 会产生不安全行为，尽量限制这些字段只读。

### 4.4 查询预览

- 只调用 `GET /query-preview`，按后端返回的 `variants` 展示：`purpose`, `canonical_query`, `included_concept_ids`, `notes`；
- 清晰标注 `term_set_version`，方便用户理解预览对应哪个词表版本；
- 当本地有**未保存草稿**时标注「预览基于已保存的后端版本，而非当前未保存修改」；
- 查询预览失败单独展示错误，不应破坏可编辑的术语草稿；
- 不在前端自行生成或翻译数据库查询串。

### 4.5 历史、导出与确认

- 历史只读：展示版本、draft/confirmed 状态、创建时间和可展开的快照；
- 历史记录与最新草稿严格分离，不自动恢复历史版本（本任务不做版本回滚）；
- CSV 调用现有 `terms/export.csv`；下载与正常编辑彼此独立；
- 确认前，至少检查启用的非排除术语覆盖两个不同概念块；后端为最终权威；
- `POST /confirm-terms` 使用当前服务器 `expected_version`；
- 用户未保存的草稿存在时，**禁止直接确认**或提示先保存，不能把旧版词表误认作刚刚编辑的结果；
- 确认成功展示状态，后续刷新或重新加载项目状态一致；
- 重新生成前必须明确提示会覆盖已保存词表，并二次确认；重新生成不会在页面载入时发生。

## 5. 状态管理与幂等交互

遵循现有 P1-01 `queue_action` / `run_pending` / `st.session_state` 模式，必要时作最小扩展：

1. 将 **服务器词表快照**、**本地编辑草稿**、**草稿脏标记**、**版本号**按项目 ID 隔离；切换项目不能混用数据。
2. 只在用户点击明确按钮时调用写接口；普通渲染或 rerun 不产生写请求。
3. 请求执行期间不能重复提交同一写入；网络超时要提醒先 `GET` 确认后端状态，而非无条件再 POST。
4. 服务器返回失败时保留草稿，错误发生后可以继续编辑和重新尝试。
5. 页面切回已确认的阶段 A 项目，能够读取该项目真实词表，而不是前一项目缓存。
6. 可以引入少量纯函数，将 `TermRecord` 变为可编辑行并转为 `TermInput`，降低 UI 复杂度，方便独立测试。

## 6. 最小测试要求

重点扩充：

- `tests/unit/test_frontend_api_client.py`
- `tests/unit/test_frontend_app.py`
- 如有必要，新建 `tests/unit/test_frontend_terms.py`（纯函数）

测试使用 `httpx.MockTransport`、Streamlit `AppTest`、临时数据库/本地 Fake API。**无需外部学术 API、LLM 密钥或真实公网检索。**

必须覆盖：

1. 阶段 A 创建、澄清、确认页面仍正常；
2. 词表未生成时，GET 只读且不会自动生成；
3. A 未确认时不可生成 B；
4. 阶段 A 确认后，POST 生成词表，状态 `draft/version=1`（由测试后端真实状态决定）；
5. 编辑已有词时保留 `term_id`，新增词不传 `term_id`；
6. 发送 PUT 时只包含允许的字段和完整术语列表，`expected_version` 正确；
7. 保存成功后刷新得到新版本；
8. 后端 `409` 版本冲突不丢草稿，也不自动重试覆盖；
9. 失败的 `422`、超时不丢草稿；
10. 预览只读、与后端版本对应，草稿未保存时有提示；
11. 历史只读，CSV 确实下载二进制内容（含中文 BOM 的适当验证）；
12. 少于两个有效概念块不能确认；有未保存编辑时不允许确认；
13. 确认后重载项目可恢复 B 状态；
14. 重复 rerun 不重复调用生成、保存、确认端点；
15. 不同 `project_id` 切换时不串词表、不串草稿；
16. 重新生成必须有明确确认，不发生隐式覆盖。

最少提供一次离线 `真实 FastAPI ASGI + Streamlit AppTest` 的 A 确认 → B 生成 → 编辑 → 预览 → 确认纵向联调；可复用现有测试工具，但不得污染正式数据库。实际浏览器交互若环境不支持，应在报告中标记「未人工验收」，不能声称已经验收。

执行并记录：

```bash
.venv/bin/python -m pytest tests/unit/test_frontend_api_client.py tests/unit/test_frontend_app.py -q
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q frontend
 git diff --check
```

可根据现有虚拟环境调整命令；`git diff --check` 前面的空格不影响 shell 执行。不要为了让测试通过而跳过失败用例。记录通过/失败/跳过数量。

## 7. 推荐文件范围

优先修改：

- `frontend/api_client.py`
- `frontend/app.py`
- `tests/unit/test_frontend_api_client.py`
- `tests/unit/test_frontend_app.py`

有清晰必要性时可以新增：

- `frontend/terms.py` 或 `frontend/components/terms.py`（不要过度拆分）
- `tests/unit/test_frontend_terms.py`
- `README.md`（增加 B 页面使用说明）

默认不要修改：

- `app/services/terms.py`、`app/schemas/terms.py`、`app/repositories/terms.py`
- `app/graph/`、`app/providers/`、`app/resolvers/`
- C/D/E 业务模块、SQLite schema 和其他无关文件

若发现**现有后端 B 的确有阻塞性 Bug**，先给出最小复现与证据；只有确实阻碍本任务且可以无破坏修复，才允许在说明原因后进行最小修改与回归测试。

## 8. 验收标准（全部满足才算 P1-02 完成）

- [ ] 能从已确认阶段 A 的项目进入阶段 B 页面。
- [ ] 可以生成并查看词表，看到概念分组、词来源、语言、类型和状态。
- [ ] 可以增加、编辑、删除词，保留已有 `term_id`。
- [ ] 保存使用 `expected_version`；版本冲突时不自动覆盖，草稿保留。
- [ ] 能查看 query preview，并清楚区分「已保存版本」和「未保存草稿」。
- [ ] 能查看历史、下载 `terms.csv`。
- [ ] 能显式确认词表；确认后刷新仍保留状态。
- [ ] 重新生成前有显式二次确认。
- [ ] 不破坏 P1-01 阶段 A 功能。
- [ ] 单元、AppTest、离线纵向联调及全量 pytest 均报告真实结果。

## 9. Codex 最终报告格式

完成后只需用中文汇报：

### 完成内容
各主要操作是否完成，是否已做 A→B 离线联调。

### 修改文件
文件路径 + 作用（简短）。

### 测试结果
具体命令、通过/失败/跳过数量；如未做真实浏览器测试，明确说明。

### 启动方式
准确的后端和前端命令（复用 P1-01），访问地址。

### 剩余问题
包括未解决的缺陷和可能影响下一步 P1-03 的限制。

**完成 P1-02 后停止，不执行 P1-03（查询计划）、P1-04（文献检索）或 P1-05（PDF 下载）。**
