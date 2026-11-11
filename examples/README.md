# 示例验收项目

`demo-project/` 是一个真实跑通的最小项目，展示「AI 自述 → 复验 → 交接」的完整链路。

| 文件 | 看什么 |
| --- | --- |
| `.receipt/contract.json` | 4 条机器可读验收契约（唯一事实源） |
| `TASK.md` | 契约的人可读镜像 |
| `report.md` | 被验收的产物 |
| `RECEIPT.md` | `verify` 生成的验收单，4 项全 PASS，附证据与复验时间 |
| `HANDOFF.md` | 自包含交接包，含状态、未决项、下一步、给下一个助手的指令 |
| `.receipt/claims.jsonl` | 一条 AI 自述，明确标注「不参与验收判定」 |
| `.receipt/events.jsonl` | init → add ×4 → attest → verify → handoff 的 append-only 事件链 |

## 复现

在仓库根目录执行（示例中的 `verify` 结果应为 `PASS 4｜FAIL 0`）：

```bash
R=./examples/demo-project
P=scripts/receipt.py

python -X utf8 $P init --root $R --title "示例：季度报告生成"
python -X utf8 $P add --root $R --id c1 --claim "报告文件已交付" --check file_exists --path report.md
python -X utf8 $P add --root $R --id c2 --claim "包含结论章节" --check file_contains --path report.md --text "## 结论"
python -X utf8 $P add --root $R --id c3 --claim "无占位符残留" --check file_contains --path report.md --text "待补充" --absent
python -X utf8 $P add --root $R --id c4 --claim "正文不少于 30 汉字" --check min_han --path report.md --min 30
python -X utf8 $P attest --root $R --id c2 --note "报告已写好（AI 自述，未验收）"
python -X utf8 $P verify --root $R
python -X utf8 $P handoff --root $R --next "复核分渠道归因口径，再重跑 verify"
python -X utf8 $P resume --root $R
```

想看证据漂移：改一下 `report.md` 再运行 `resume`，相关项会被标成 `STALE`，旧验收单不再可用。
