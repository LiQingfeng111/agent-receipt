# 交接包 · 示例：季度报告生成

- 生成时间：2026-11-11 23:19:35
- 事实源：`.receipt/contract.json`（4 条验收项）
- 本文件自包含：不依赖聊天记录、不依赖任何特定 AI 工具。

## 当前状态

- 上次复验：2026-11-11 23:19:35｜结论 **PASS**
- PASS 4｜FAIL 0｜UNVERIFIED 0

| ID | 承诺 | 结果 | 证据 |
| --- | --- | --- | --- |
| c1 | 报告文件已交付 | PASS | report.md 存在 |
| c2 | 包含结论章节 | PASS | report.md：命中 文本 '## 结论' |
| c3 | 无占位符残留 | PASS | report.md：命中 不应出现 文本 '待补充' |
| c4 | 正文不少于 30 汉字 | PASS | 汉字数 198 / 要求 ≥ 30（排除 frontmatter 与注释） |

## 证据有效性

交接时检测到证据过期的验收项：无

## 未决事项

- 无（仅指契约内检查项，不代表任务意图已满足）。

## AI 自述声明（未经验收，仅供参考）

- `2026-11-11 23:19:34` c2：报告已写好，结论章节也补上了（AI 自述，未验收）

## 下一步

复核分渠道归因口径，再重跑 verify

## 给下一个助手的指令

1. 先读完本文件，再读 `TASK.md` 与 `.receipt/contract.json`。
2. 运行 `python -X utf8 <receipt.py> resume --root <项目根>` 重新复验；不要继承这里的 PASS 结论。
3. 对 FAIL / UNVERIFIED / STALE 逐项处理，处理后再 `verify`。
4. 不要在未重新复验的情况下宣布任务完成；自述用 `attest` 记录即可。

## 文件清单

- `TASK.md` 任务契约（人读）｜`RECEIPT.md` 验收单｜`.receipt/contract.json` 契约（事实源）
- `.receipt/claims.jsonl` 自述声明｜`.receipt/events.jsonl` 事件日志
