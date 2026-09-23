# 阶段 D 操作指南

阶段 E 检测指南中的“先完成阶段 D”，是指先通过 API 执行一次真实文献检索，让数据库生成 `works` 记录。阶段 E 使用的 `PROJECT_ID` 和 `WORK_ID` 都来自这里。

完整流程为：

```text
确认阶段 B → 编译并确认阶段 C → 执行阶段 D → 查询 OA 文献 → 进入阶段 E
```

## 1. 启动服务

先配置项目根目录下的 `.env`：

```dotenv
CROSSREF_MAILTO=你的真实邮箱
UNPAYWALL_EMAIL=你的真实邮箱
LITERATURE_HTTP_USER_AGENT="LiteratureSearchAgent/0.5 (mailto:你的真实邮箱)"

# 有密钥时填写
OPENALEX_API_KEY=
SEMANTIC_SCHOLAR_API_KEY=
```

启动服务：

```bash
cd /home/zjzhang/Desktop/literature/Literature-Search-agent

set -a
source .env
set +a

.venv/bin/literature-clarifier --reload
```

打开交互式 API 文档：

```text
http://127.0.0.1:8000/docs
```

## 2. 确认阶段 B 已完成

查询当前词表：

```bash
curl -s http://127.0.0.1:8000/projects/PROJECT_ID/terms
```

将 `PROJECT_ID` 替换为创建项目时得到的真实 ID。

如果返回内容中包含：

```json
{
  "status": "confirmed",
  "version": 1
}
```

说明阶段 B 已完成，可以进入阶段 C。

如果状态是 `draft`，使用响应中的实际 `version` 确认词表：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/confirm-terms \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":1}'
```

## 3. 编译并确认阶段 C 查询计划

第一次建议每个数据源只检索少量记录：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/query-plans/compile \
  -H 'Content-Type: application/json' \
  -d '{
    "providers":["openalex","crossref","semantic_scholar"],
    "page_size":20,
    "max_records":20,
    "replace_existing":false
  }'
```

响应中会包含查询计划的状态、版本和各数据源查询，例如：

```json
{
  "status": "draft",
  "version": 1,
  "queries": []
}
```

确认查询计划。这里的 `expected_version` 必须使用编译响应中的实际版本：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/confirm-query-plan \
  -H 'Content-Type: application/json' \
  -d '{"expected_version":1}'
```

确认成功后，状态应为：

```json
{
  "status": "confirmed",
  "version": 1
}
```

如果编译时返回 HTTP `409`，通常表示查询计划已经存在。先查看当前计划：

```bash
curl -s http://127.0.0.1:8000/projects/PROJECT_ID/query-plans
```

- 已有计划是 `confirmed`：不需要重新编译。
- 已有计划是 `draft`：使用它的 `version` 调用确认接口。
- 确实需要重新生成：再次调用编译接口并将 `replace_existing` 改为 `true`。

## 4. 执行阶段 D

启动一次真实的首轮检索：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/search-runs \
  -H 'Content-Type: application/json' \
  -d '{
    "purposes":["broad"],
    "max_records_per_query":20
  }'
```

该接口会访问真实的 OpenAlex、Crossref 和 Semantic Scholar，可能需要等待数秒。

成功响应中重点检查以下字段：

```json
{
  "run_id": "检索运行 ID",
  "status": "completed",
  "raw_record_count": 60,
  "work_count": 45,
  "error_count": 0
}
```

状态含义：

- `completed`：全部数据源检索成功。
- `partial`：部分数据源失败，但成功的数据已经保存。
- `failed`：所有数据源都失败。
- `work_count > 0`：已经产生阶段 E 可以使用的文献记录。

如果只想先验证阶段 D 的基本流程，可以仅执行 Crossref：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/search-runs \
  -H 'Content-Type: application/json' \
  -d '{
    "providers":["crossref"],
    "purposes":["broad"],
    "max_records_per_query":20
  }'
```

前提是阶段 C 的查询计划中包含 Crossref 查询。

## 5. 查看阶段 D 产生的文献

查看全部文献：

```bash
curl -s \
  'http://127.0.0.1:8000/projects/PROJECT_ID/works?limit=20'
```

优先查看开放获取文献：

```bash
curl -s \
  'http://127.0.0.1:8000/projects/PROJECT_ID/works?is_oa=true&limit=20'
```

响应示例：

```json
{
  "total": 12,
  "items": [
    {
      "work_id": "7bbf...",
      "title": "文献标题",
      "doi": "10.xxxx/xxxx",
      "pmcid": "PMC...",
      "arxiv_id": null,
      "is_oa": true,
      "pdf_url": "https://example.org/paper.pdf"
    }
  ]
}
```

其中：

- 项目 ID 是阶段 E 使用的 `PROJECT_ID`。
- `items[].work_id` 是阶段 E 使用的 `WORK_ID`。

也可以把阶段 D 的结果导出为 CSV：

```bash
curl -s \
  http://127.0.0.1:8000/projects/PROJECT_ID/exports/works.csv \
  -o works.csv
```

## 6. 处理部分失败和恢复

查看某次检索运行的详情：

```bash
curl -s \
  http://127.0.0.1:8000/projects/PROJECT_ID/search-runs/RUN_ID
```

如果状态是 `partial` 或 `failed`，检查响应中每个 `query_executions` 的 `error` 字段。网络或限流问题解除后，可以恢复运行：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/PROJECT_ID/search-runs/RUN_ID/resume
```

恢复操作会从已保存的游标继续，不需要重新创建项目或查询计划。

## 7. 进入阶段 E

假设阶段 D 返回：

```text
PROJECT_ID=abc123
WORK_ID=def456
```

先进行下载估算：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/abc123/downloads/estimate \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["def456"]}'
```

确认 `eligible_count=1` 后执行下载：

```bash
curl -s -X POST \
  http://127.0.0.1:8000/projects/abc123/downloads \
  -H 'Content-Type: application/json' \
  -d '{"mode":"selected","work_ids":["def456"]}'
```

为了提高阶段 E 的成功率，优先选择具备以下任一条件的记录：

1. 有 `arxiv_id`；
2. 有 `pmcid`；
3. `is_oa=true` 且有 DOI；
4. `is_oa=true` 且有可信的 `pdf_url`。

## 8. 阶段 D 完成标准

- [ ] 阶段 B 词表状态为 `confirmed`；
- [ ] 阶段 C 查询计划状态为 `confirmed`；
- [ ] `POST /search-runs` 返回 `completed`，或返回有可用结果的 `partial`；
- [ ] `work_count > 0`；
- [ ] `GET /works` 能返回文献记录；
- [ ] 最好至少有一条记录包含 `arxiv_id`、`pmcid`，或 `is_oa=true` 且有 DOI。

完成以上检查后，即可按照《阶段 E 检测指南》继续单篇真实 OA 冒烟测试。
