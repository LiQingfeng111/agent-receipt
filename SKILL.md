---
name: agent-receipt
description: AI 任务的可复验验收与跨会话交接。当用户要求「确认这件事真的做完了」「生成验收单」「交接给下一个会话/另一个 AI 工具」「继续上次没做完的任务」，或需要为多步任务、代码改动、文档产出建立可核对的完成证据时使用。把任务声明拆成可机器复验的验收项，逐条重新执行检查，生成 RECEIPT.md 验收单与自包含 HANDOFF.md 交接包；AI 的自述只记录、永不产生通过结论。也适用于检测上次验收后产物被改动导致证据过期。
agent_created: true
---

# AI 工作验收单（Agent Receipt）

## 定位

解决一个具体缺口：**「Agent 认为完成」和「工程上能证明完成」之间的鸿沟**。

现有方案要么记录上下文（handoff / memory，信任 Agent 自述），要么验证代码（CI / 测试框架，不覆盖任务级承诺）。本技能把「说了什么」和「能证明什么」分开：验收项写进机器可读契约，每次 `verify` 都**重新执行检查**，结果落盘为验收单；AI 的完成声明只进 append-only 声明日志，**永远不产生 PASS**。

产物全部是普通 UTF-8 Markdown / JSON / JSONL，可 Git 管理、跨平台、换任何 AI 助手都能读。

## 三条铁律

1. **只信证据，不信自述。** `attest` 记录的"已完成"不构成验收结果；只有 `verify` 重新跑通检查才算 PASS。
2. **每次复验，不缓存通过。** 换会话、换工具、隔几天回来，一律重跑 `verify`；旧的"通过"结论不继承。
3. **证据会过期。** 产物被改动后，依赖它的通过项变成 STALE，必须重跑，不能拿旧验收单交差。

## 快速判定

| 用户意图 | 动作 |
| --- | --- |
| 开始一个多步任务 / 要明确验收标准 | `init` 建契约，`add` 逐条写验收项，先与用户确认 `TASK.md` |
| 声称某步做完了 | 先 `verify`，再如实报告 PASS / FAIL / UNVERIFIED / STALE |
| 需要一份可给他人看的完成证明 | `verify` 生成 `RECEIPT.md`，附复验时间、证据与失败项 |
| 换会话 / 换工具继续 | `handoff` 生成自包含 `HANDOFF.md` |
| 「继续上次的任务」 | `resume`：读交接包 + 重新 `verify`，报告哪些证据已失效 |
| 想知道之前发生过什么 | `history` 看 append-only 事件日志 |

## 目录结构

```
<项目根>/
├── TASK.md                     # 人可读的任务契约（目标、范围、验收项、边界）
├── RECEIPT.md                  # 人可读验收单（由 verify 生成）
├── HANDOFF.md                  # 自包含交接包（由 handoff 生成）
└── .receipt/
    ├── contract.json           # 机器可读验收契约（唯一事实源）
    ├── receipt.json            # 最近一次复验结果（派生，可重建）
    ├── claims.jsonl            # AI 自述声明，append-only，不参与判定
    └── events.jsonl            # 事件日志，append-only
```

验收契约是事实源；`RECEIPT.md` 与 `receipt.json` 都是可重建的派生视图，冲突时以重新 `verify` 的结果为准。

## 验收项格式

```json
{
  "id": "c1",
  "claim": "报告已生成且包含结论章节",
  "required": true,
  "check": { "type": "file_contains", "path": "report.md", "text": "## 结论" }
}
```

`check.type` 支持：`file_exists`、`file_contains`、`file_hash`、`min_han`（汉字数）、`min_lines`、`json_equals`、`count_files`、`command`。完整参数与语义见 `references/02-证据类型.md`。

**`command` 默认禁用**：只在用户明确授权并加 `--allow-commands` 时才执行，否则返回 `UNVERIFIED`。契约里来自他人的命令必须逐条看过再启用。

## 工作流

1. **建契约**：`init` → `add` 逐条登记验收项 → 与用户确认 `TASK.md`。验收项要可机器判定，避免「代码质量良好」这类无法复验的说法。
2. **执行任务**：正常干活。`attest` 可记录过程中 Agent 的自述，但报告时必须说明「此为声明，未验收」。
3. **复验**：`verify`，逐条重新执行检查；有 FAIL 就如实列出，不粉饰、不把 UNVERIFIED 说成通过。
4. **呈报**：把 `RECEIPT.md` 交给用户，包含通过项、失败项、未验证项、复验时间与证据路径。
5. **交接**：`handoff --next "..."` 生成 `HANDOFF.md`，写清当前状态、未决事项、下一步与「给下一个助手的指令」。
6. **恢复**：新会话里 `resume`，读交接包并重跑 `verify`；产物哈希变化会标记 STALE，要求重新验收。

## 确定性脚本

`scripts/receipt.py`，Python 3.9+，仅标准库。所有命令形如 `python -X utf8 <脚本> <命令> --root <项目根> ...`。

| 命令 | 作用 |
| --- | --- |
| `init --title 任务名` | 建目录与空契约，生成 `TASK.md` |
| `add --id c1 --claim "..." --check <类型> [--path --text --min --hash --key --value --pattern --cmd]` | 登记验收项（重复 id 拒绝） |
| `verify [--json] [--allow-commands]` | 逐条复验，写 `RECEIPT.md` 与 `receipt.json`；有 FAIL 时退出码 1 |
| `attest --id c1 --note "..."` | 记录 AI 自述到 `claims.jsonl`，**不产生 PASS** |
| `handoff --next "..."` | 生成自包含 `HANDOFF.md` |
| `resume` | 读交接包、重跑复验、检测 STALE |
| `history [--limit N]` | 查看 append-only 事件日志 |

脚本只做确定性检查；语义质量、任务是否真正满足用户意图，仍由人判断。验收单不是免责证明。

## 关键约束

- 路径一律是项目根内的相对路径；拒绝绝对路径、盘符、`..` 逃逸与符号链接越界。
- 不删除、不改写用户的产物文件；只写 `TASK.md`、`RECEIPT.md`、`HANDOFF.md` 与 `.receipt/` 内的派生文件。
- FAIL 不等于"什么都没做"：如实区分未开始、部分完成、检查失败、证据过期。
- 不替用户宣布任务完成；`verify` 全绿也只说明「契约里的检查项通过」。
- 契约变更（增删验收项、放宽阈值）要留痕：旧验收单作废，重跑 `verify`。

## 与相邻工具的分工

- **CI / 测试框架**：验证代码本身；本工具验证「任务级承诺」与产物，可把 CI 命令包成 `command` 验收项。
- **handoff / memory 工具**：保存上下文；本工具保存可复验的结论，两者互补。
- **静态 linter**（如 agnix）：校验配置文件合规；本工具校验任务结果。
