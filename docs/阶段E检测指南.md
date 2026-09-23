# 阶段 E 检测指南

本指南按风险从低到高分四层验证：离线单元测试、API 模拟测试、单篇真实开放全文、受控批量测试。不要一开始直接执行 `all_oa`。

## 1. 环境准备

```bash
cd /home/zjzhang/Desktop/literature/Literature-Search-agent
cp .env.example .env
```

至少在 `.env` 中填写一个有效联系邮箱：

```dotenv
UNPAYWALL_EMAIL=your-real-email@example.com
LITERATURE_HTTP_USER_AGENT="LiteratureSearchAgent/0.5 (mailto:your-real-email@example.com)"
```

`CORE_API_KEY` 是可选项。没有它时，元数据、Unpaywall、Europe PMC 和 arXiv 仍可工作。

## 2. 第一层：全离线测试

先执行阶段 E 专项测试：

```bash
.venv/bin/pytest tests/unit/test_downloads.py tests/unit/test_resolvers.py -vv
```

再执行 A-E 全量回归：

```bash
.venv/bin/pytest -vv
.venv/bin/pytest --cov=app --cov-report=term-missing
```

当前基线应为 `40 passed`。专项测试覆盖：

- 429 重试及 `Retry-After`；
- HTML 登录页和伪 PDF 拒绝；
- `%PDF-` 文件头校验；
- 私网/HTTP URL 拒绝；
- resolver 失败后的顺序降级；
- `.part` 原子写入和清理；
- SHA-256 去重；
- 重复执行幂等；
- 中断后恢复；
- manifest、失败 CSV 和 URL token 脱敏。

## 3. 第二层：启动服务并确认接口

```bash
set -a
source .env
set +a
.venv/bin/literature-clarifier --reload
```

打开 <http://127.0.0.1:8000/docs>，确认 `phase-e` 下存在六个接口。健康检查应返回：

```json
{"status":"ok","phases":"A,B,C,D,E"}
```

## 4. 第三层：单篇真实 OA 冒烟测试

先完成阶段 D，并从下面的接口选择一条明确具有 `arxiv_id`、`pmcid`，或 `is_oa=true` 且有 DOI 的记录：

```text
GET /projects/{project_id}/works
```

### 4.1 查看估算

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/downloads/estimate \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["WORK_ID"]}'
```

确认 `eligible_count=1`，再执行下载：

```bash
curl -s -X POST http://127.0.0.1:8000/projects/PROJECT_ID/downloads \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["WORK_ID"]}'
```

可接受结果：

- `downloaded`：本次成功下载；
- `already_exists`：相同文献或相同 SHA-256 已存在；
- `manual_access_required`：没有发现可合法自动获取的版本；
- `failed`：网络或解析器发生可重试的技术错误。

### 4.2 校验本地文件

```bash
find workspace/projects/PROJECT_ID/papers -maxdepth 1 -name '*.pdf' -type f
head -c 5 workspace/projects/PROJECT_ID/papers/FILE.pdf
sha256sum workspace/projects/PROJECT_ID/papers/FILE.pdf
```

第二条命令必须输出 `%PDF-`。响应中的 `sha256` 应与 `sha256sum` 一致，并且目录中不能残留 `*.part`。

### 4.3 校验审计报告

```text
GET /projects/{project_id}/downloads/{run_id}/manifest.jsonl
GET /projects/{project_id}/downloads/{run_id}/failed.csv
```

检查 manifest 是否包含：`work_id`、最终状态、来源、license、版本类型、SHA-256、本地路径和每个 resolver 的尝试。URL 不应包含 `token`、`api_key` 或查询字符串。

### 4.4 校验幂等

对同一个 `WORK_ID` 再执行一次 `selected`。预期：

- 状态为 `already_exists`；
- `papers/` 中 PDF 数量不增加；
- SHA-256 不变。

## 5. 第四层：受控批量测试

先用较小的 `top_n_oa`：

```json
{"mode":"top_n_oa","top_n":5}
```

确认 5 篇的状态、磁盘占用、失败原因和速率表现后，再估算 `all_oa`：

```json
{"mode":"all_oa"}
```

估算会返回 `requires_confirmation=true`。确认文件数和预计空间后才能执行：

```json
{"mode":"all_oa","confirm_all_oa":true}
```

## 6. 恢复和故障验证

如果某次运行是 `partial` 或 `failed`，网络恢复后调用：

```text
POST /projects/{project_id}/downloads/{run_id}/resume
```

成功项不会重复下载，只会重试 `pending`、`failed` 和 `manual_access_required` 项。

建议额外检查这些错误路径：

1. 给 `selected` 传入其他项目的 `work_id`，应返回 422；
2. 未确认直接执行 `all_oa`，应返回 409；
3. 使用明确付费且无 OA 版本的 DOI，应标记 `manual_access_required`；
4. 临时断网后恢复运行，不应产生重复 PDF；
5. 将单文件限制调小，超限文件应失败且不残留 `.part`。

## 7. 验收清单

- [ ] 40 项自动测试全部通过；
- [ ] 至少一篇 arXiv 或 Europe PMC 文献真实下载成功；
- [ ] PDF 文件头和 SHA-256 校验一致；
- [ ] 同一项目所有 PDF 位于同一个 `papers/` 文件夹；
- [ ] 重复执行不会重复保存相同文件；
- [ ] manifest 记录来源、许可、版本、哈希与尝试链；
- [ ] 付费文献不绕过访问控制，显示 `manual_access_required`；
- [ ] 429/5xx 可重试，401/403/451 不无限重试；
- [ ] `all_oa` 未确认时不能执行；
- [ ] API key、临时 token 和 URL 查询参数未写入报告。
