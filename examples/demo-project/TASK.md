# 示例：季度报告生成

> 人可读视图。机器可读的唯一事实源是 `.receipt/contract.json`；两者冲突时以契约为准。

- 创建时间：2026-11-11 23:19:13
- 验收项：4 条
- 判定规则：只有 `verify` 重新执行检查才产生 PASS；AI 自述只进 `claims.jsonl`。

## 验收项

| ID | 承诺 | 检查类型 | 参数 | 必需 |
| --- | --- | --- | --- | --- |
| c1 | 报告文件已交付 | file_exists | path=report.md | 是 |
| c2 | 包含结论章节 | file_contains | path=report.md、text=## 结论 | 是 |
| c3 | 无占位符残留 | file_contains | path=report.md、text=待补充、absent=True | 是 |
| c4 | 正文不少于 30 汉字 | min_han | path=report.md、min=30 | 是 |

## 边界

- 验收项必须可机器判定；「质量良好」「看起来没问题」这类表述不要写进来。
- 契约变更（增删验收项、放宽阈值）会使旧验收单作废，需重跑 `verify`。
- `command` 类型默认不执行，需显式 `--allow-commands` 授权。
