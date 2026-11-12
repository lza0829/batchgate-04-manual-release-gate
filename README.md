# 02-batchgate（闸序）

Batchgate — 离线作业依赖编排：作业定义 → 依赖就绪检查 → 人工放行闸 → 执行/失败重试 → 产出水位与对账。

与 Feedstitch（多源 Feed 归并）主链路不同，禁止做成 Feed 解析/冲突/回看换皮。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5200 |
| API | http://localhost:10200 |

## 主链

配置作业与依赖 → 触发一次编排 Run → 按依赖层执行夹具作业 → 关键作业在放行点等待人工放行 → 放行/拒绝后继续 → 写入尝试记录与产出水位 → 报告对账

## 人工放行闸（manual release gate）

- **作业标记**：作业可勾选「需放行」（`jobs.requires_release`，作业页可改，`PATCH /api/jobs/{id}/release-gate`）。种子中 `fact_order_daily` 默认带闸。
- **分阶段执行**：`POST /api/orchestrations` 开一趟编排，按依赖层执行；碰到未放行的闸作业时**不跑夹具**，记 `skipped_release`，编排停在 `awaiting_release`（未写 `finished_at`）。
- **可审计决定**：`POST /api/orchestrations/{id}/releases`（body：`job_id`、`decision`=released/rejected、`actor`、`note`）写入 `release_decisions` —— 对哪趟编排的哪个作业、谁、何时、什么决定；每趟每作业唯一、不可改。仅 `awaiting_release` 状态接受决定。
- **继续**：`POST /api/orchestrations/{id}/resume` 重评剩余作业：已放行 → 执行夹具并推进水位；已拒绝 → 本趟不再执行，记 `skipped_release`（gate=rejected，含决定人/时间/备注）；其下游按依赖规则记 `skipped_dep` 并写清上游原因。
- **对账**：报告的成功/失败/放行跳过/依赖跳过 = 每作业本趟最近一次尝试记录汇总；放行/拒绝计数 = 放行记录汇总。尝试记录页与编排台均可看到闸结果。
- 决定只来自人工操作：不按上游行数自动选分支，不做无人值守的质量评分自动抬水位。

## 种子说明

- **ingest_orders**：无依赖，写出订单水位。
- **dim_customer**：无依赖，写出客户维表水位。
- **fact_order_daily**：依赖上两者成功且当趟人工放行后才执行（底座仅做“上游成功”检查，严格水位闸与重试预算留给 0-1 题）。

## 演示流程

1. 编排台「执行编排」→ 停在 `awaiting_release`，`fact_order_daily` 记 `skipped_release`（未放行跳过）。
2. 在闸口面板「放行」→「继续编排」→ 该作业执行成功，编排 `ok`（放行后执行成功）。
3. 再执行一趟 → 在闸口面板「拒绝」→「继续编排」→ 该作业记 `skipped_release`（rejected），本趟不再执行（拒绝）。
4. 在作业页给 `ingest_orders` 也挂闸后执行编排 → 其下游 `fact_order_daily` 记 `skipped_dep` 并写清原因（依赖跳过）。

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现）

`dep_gate` / `retry_budget` / `watermark_reconcile` / `sla_breach`
