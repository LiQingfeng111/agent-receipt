# Agent Receipt · AI 工作验收单

> **AI 说「做完了」，凭什么信？**
>
> 一套零依赖、平台无关的协议 + CLI：把任务承诺拆成可机器复验的验收项，每次重新执行检查，生成人可读验收单与自包含交接包。AI 的自述只记录，永不产生通过结论。

[English summary](#english-summary) · [快速开始](#快速开始) · [证据类型](#证据类型) · [为什么做这个](#为什么做这个)

## 为什么做这个

2026 年，让 AI 干活已经不难，难的是**验收**：

- 助手说"测试都跑过了"，但没留下任何命令输出；
- 助手说"报告已生成"，文件其实是上一版，或者里面还留着"待补充"；
- 换了会话/换了工具，之前那些"完成"结论全部成了无法核对的聊天记录；
- 交接文档写得很好，但没人能验证里面写的是不是真的。

现有方案分两类，都没覆盖这个缺口：

| 已有方案 | 解决什么 | 没解决什么 |
| --- | --- | --- |
| handoff / memory / checkpoint 工具（多为 0–3 星小项目） | 保存上下文与状态 | **信任 Agent 自述**，不验证产物 |
| CI、测试框架、静态 linter（如 `agent-sh/agnix` ★478） | 验证代码与配置 | 不覆盖「任务级承诺」与交付产物 |
| 企业内证据链方案、2026 年 11 月的概念文章 | 提出「证据契约」思路 | 没有可直接拿来用的、平台无关的轻量开源实现 |

`agent-receipt` 填的就是这个空：**声明与证据分离 + 每次复验 + 证据漂移检测 + 自包含交接**。

## 核心设计

1. **只信证据，不信自述**：`attest` 把 AI 的"已完成"写进 append-only 日志，但它永远不产生 PASS。
2. **每次重新跑**：`verify` 不缓存通过结论，逐条重新执行检查，附复验时间与证据。
3. **证据会过期**：验收时记录产物 SHA-256；`resume` 发现文件变了就把该项标为 `STALE`，旧验收单不能交差。
4. **交接包自包含**：`HANDOFF.md` 是纯 Markdown，含状态、未决项、下一步和「给下一个助手的指令」，换 Claude Code / WorkBuddy / Codex / 人类同事都能读。
5. **零依赖**：Python 3.9+ 标准库，不要 API Key、不要数据库、不要模型调用、不联网。

## 快速开始

### 安装

**WorkBuddy 技能**：

```bash
git clone https://github.com/LiQingfeng111/agent-receipt.git \
  "$HOME/.workbuddy-ai/skills/agent-receipt"
```

项目级使用放 `<项目>/.workbuddy-ai/skills/agent-receipt/`。重启会话后，说「帮我给这个任务建验收项」「确认一下真的做完了」「交接给下一个会话」即可触发。

**独立 CLI**（不装 WorkBuddy 也能用）：

```bash
python -X utf8 scripts/receipt.py init --root ./my-task --title "重构登录模块"
```

### 一个完整例子

```bash
R=./my-task
P=scripts/receipt.py

# 1. 建契约，登记三条可机器判定的验收项
python -X utf8 $P init --root $R --title "重构登录模块"
python -X utf8 $P add --root $R --id c1 --claim "登录页文件存在" --check file_exists --path src/login.py
python -X utf8 $P add --root $R --id c2 --claim "不再使用旧接口" --check file_contains --path src/login.py --text "def sign_in"
python -X utf8 $P add --root $R --id c3 --claim "测试通过" --check command --cmd "python -m unittest discover -s tests" --required

# 2. 干活。AI 声称完成时只记录声明，不算验收
python -X utf8 $P attest --root $R --id c2 --note "已替换旧接口（AI 自述，待复验）"

# 3. 复验（command 类型需显式授权）
python -X utf8 $P verify --root $R --allow-commands

# 4. 交接给下一个会话 / 另一个工具
python -X utf8 $P handoff --root $R --next "补上异常分支的测试，然后重新 verify"

# 5. 新会话里恢复
python -X utf8 $P resume --root $R
```

`verify` 输出 `RECEIPT.md`：

| ID | 声明 | 结果 | 证据 | 复验时间 |
| --- | --- | --- | --- | --- |
| c1 | 登录页文件存在 | PASS | `src/login.py` 存在，SHA-256 `a1b2c3…` | 2026-11-11 23:10:02 |
| c2 | 不再使用旧接口 | FAIL | 未找到 `def sign_in` | 2026-11-11 23:10:02 |
| c3 | 测试通过 | UNVERIFIED | command 类型未获授权 | — |

有 FAIL 时 `verify` 退出码为 1，可直接接进 CI。

## 证据类型

| 类型 | 判定 | 典型用途 |
| --- | --- | --- |
| `file_exists` | 文件存在且是普通文件 | 产物是否交付 |
| `file_contains` | 含指定文本或正则 | 关键章节、接口名、无占位符（配 `--absent`） |
| `file_hash` | SHA-256 匹配 | 产物未被悄悄替换 |
| `min_han` | 汉字数 ≥ N（排除 frontmatter 与注释） | 中文稿件篇幅 |
| `min_lines` | 非空行数 ≥ N | 内容不是空壳 |
| `json_equals` | JSON 某字段等于期望值 | 配置、状态字段 |
| `count_files` | 匹配 glob 的文件数 ≥ N | 批量产物数量 |
| `command` | 命令退出码为 0（可选输出包含文本） | 测试、构建、lint |

**安全边界**：`command` 默认禁用，必须 `--allow-commands`；所有路径限制在项目根内；脚本不删除、不改写用户产物；不使用 `eval`、不联网、不调用模型。

## 目录结构

```
<项目根>/
├── TASK.md          # 人可读任务契约
├── RECEIPT.md       # 人可读验收单（verify 生成）
├── HANDOFF.md       # 自包含交接包（handoff 生成）
└── .receipt/
    ├── contract.json    # 验收契约（唯一事实源）
    ├── receipt.json     # 最近一次复验结果（派生）
    ├── claims.jsonl     # AI 自述，append-only
    └── events.jsonl     # 事件日志，append-only
```

`RECEIPT.md` / `receipt.json` / `HANDOFF.md` 都可重建；冲突时以重新 `verify` 为准。

## 仓库内容

```
SKILL.md                  WorkBuddy 技能入口
README.md                 本文件
scripts/receipt.py        确定性 CLI（仅标准库）
references/
  01-协议.md              契约、状态机、事件日志
  02-证据类型.md          每种检查的参数与判定规则
  03-交接与恢复.md        HANDOFF.md 规范、STALE 处理、跨工具用法
examples/demo-project/    一个跑通的完整示例
tests/test_receipt.py     单元测试
```

## 与其他项目

本项目与同作者的 [`workbuddy-webnovel-writer`](https://github.com/LiQingfeng111/workbuddy-webnovel-writer) 共享设计哲学（文件即事实源、确定性检查、不信任自述），但面向通用任务，两者互不依赖。配套项目 [`file-plan`](https://github.com/LiQingfeng111/file-plan) 管的是**文件副作用**：执行前先生成可哈希绑定的操作计划。

调研过的相邻工作：`agent-sh/agnix`（静态配置 lint，760 条规则）、`skillmds/SkillMD`（技能 lint/安装）、`common-context-mcp` / `agent_ariadne` / `praukron`（上下文与任务状态记录）、`mohamed-khairy-5i/trigger-doctor`（技能触发测试）。它们解决的是别的问题，本项目不重复造轮子，也未复制其代码或文档。

## 边界与 FAQ

**Q：全绿就等于任务完成了吗？** 不等于。验收单只说明「契约里的检查项通过」，语义质量与用户意图仍需人判断。写得不好的验收项会给出虚假安全感，所以契约要由人确认。

**Q：能替代 CI 吗？** 不能，可把 CI 命令包成 `command` 验收项，作为任务级的统一入口。

**Q：为什么 `attest` 存在？** 为了留痕和对照——事后能看出「AI 当时声称做了什么」与「实际复验结果」的差距，这是复盘最有用的数据。

**Q：换工具真的能读吗？** 交接包是纯 Markdown，不含私有字段；把 `HANDOFF.md` 贴给任何助手即可。

**Q：会不会误删文件？** 不会。脚本从不删除或改写用户产物，只写自己的四个派生文件与 `.receipt/`。

## English Summary

A zero-dependency, platform-agnostic protocol and CLI for verifying AI agent work. Task commitments are written as machine-checkable acceptance items; every `verify` re-runs all checks and emits a human-readable receipt plus a self-contained Markdown handoff. Agent self-reports are logged but never produce a PASS. Artifact hashes let `resume` detect stale evidence after files change. Standard-library Python only; no model calls, network, or database.

## 许可证

[MIT](LICENSE) © 2026 LiQingfeng111
