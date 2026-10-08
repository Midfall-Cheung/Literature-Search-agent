# P0 验收与兼容说明

## 自动 API 验收（无需 API Key）

在根目录执行（先隔离 `app.main` 导入时创建的默认应用，避免接触真实 `workspace/`）：

```bash
p0_test_root=$(mktemp -d /tmp/literature-p0-tests.XXXXXX)
export LITERATURE_DATABASE_URL="sqlite:///$p0_test_root/import-business.db"
export LITERATURE_CHECKPOINT_DB="$p0_test_root/import-checkpoints.db"
export LITERATURE_PROJECTS_ROOT="$p0_test_root/projects"
.venv/bin/python -m pytest -v tests/unit/test_api.py::test_invalid_answer_and_revision_preserve_state_and_resume
```

此测试使用 `tmp_path` 下的业务数据库和 checkpoint，经真实 FastAPI 路由完成以下步骤：

1. `POST /projects`，请求 `{"original_question":"大语言模型能否改善高校教师的文献检索效率？"}`。
2. 每轮读取响应的 `current_question.target_fields`，按字段回答：对象“高校教师”、核心概念“大语言模型”、结果“文献检索效率”、研究类型“不限”；日期目标回答 `2025—2026`。
3. 语言目标先发送 `{"content":"[2025,2026]"}` 到 `/projects/{id}/messages`。应返回 409 和语言输入提示；业务数据、轮次、历史消息及 checkpoint 均不变。
4. 重建应用后发送 `{"content":"中英文"}`，应可恢复流程。确认结构化数据为 `date_from=2025`、`date_to=2026`、`languages=["zh","en"]`。
5. 等待确认时，发送 `{"accepted":false,"field_updates":{"outcomes":["文献检索效率"]}}` 到 `/projects/{id}/confirm-question`。检查新摘要及结构化字段，状态仍为 `awaiting_confirmation`，`confirmed=false`。
6. 再发送 `{"accepted":true}`，应进入 `confirmed`。同时提交确认与非空字段修订会返回 409，需先修订再确认。

在同一终端中运行全部相关测试：

```bash
.venv/bin/python -m pytest -q tests/unit/test_clarifier.py tests/unit/test_api.py
.venv/bin/python -m pytest -q
```

## 规则与历史记录

- 语言按首次出现顺序去重，小写规范代码；支持 `zh/en/ja/fr/de/es/ko/ru/pt/it/ar/hi` 及解析器中的明确名称别名。模型和显式更新只接受合法代码列表，拒绝年份、空白项和混合无效项。新增语言需扩充明确白名单，无新依赖。
- 年份在 1500～2100 范围内；有范围连接符时保留方向并拒绝反向区间，无顺序语义的双年份按升序处理；超过两个候选年份及不完整区间拒绝。
- “不限”清空对应值；具体修订清除对应旧标记；同次矛盾更新拒绝。校验在恢复 LangGraph 前完成，错误输入不会保存在 checkpoint。
- API 请求 JSON 结构、数据库表及六轮确认流程保持不变。初始问题解析错误返回 409，且不会留下半成品项目。
- 严格语言和“不限”一致性校验可能令历史污染记录无法读取或继续。阶段 A 状态查询/回答/确认会返回 409，明确提示历史数据校验失败；不存在的项目仍返回 404。其他读取相同模型的调用也可能遇到校验错误。
- 本次不扫描或迁移真实数据库。修复旧记录应先备份业务数据库和 checkpoint，人工确定真实语言/边界，经单独授权同步修复当前记录、受影响历史版本及对应 checkpoint；不得猜测年份代表的语言，也不得只修一侧造成流程不一致。也可保留原记录，另建干净项目重新澄清。
- 临时脏记录回归证明原值未被静默改写，干净新项目不受污染。
