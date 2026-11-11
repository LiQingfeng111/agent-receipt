#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent Receipt - re-verifiable acceptance evidence for AI agent work.

Standard library only. The contract (.receipt/contract.json) is the single
source of truth. Agent self-reports (claims.jsonl) are append-only records
that NEVER produce a PASS. Every verify re-runs all checks from scratch.

    python -X utf8 receipt.py <command> --root <project-root> [options]
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path

FORMAT_VERSION = 1
HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
FM_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)

PASS, FAIL, UNVERIFIED, STALE = "PASS", "FAIL", "UNVERIFIED", "STALE"
CHECK_TYPES = (
    "file_exists", "file_contains", "file_hash", "min_han", "min_lines",
    "json_equals", "count_files", "command",
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8-sig")


def write_text(p: Path, text: str, atomic: bool = False) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if atomic:
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    else:
        p.write_text(text, encoding="utf-8", newline="\n")


def json_dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def han_count(text: str) -> int:
    body = COMMENT_RE.sub("", FM_RE.sub("", text))
    return len(HAN_RE.findall(body))


def safe_rel(root: Path, rel: str) -> Path:
    """Resolve a repository-relative path; reject traversal and absolute paths."""
    p = Path(rel)
    if not rel or p.is_absolute() or p.drive or ".." in p.parts:
        raise ValueError(f"不安全的路径（需为项目内相对路径）: {rel}")
    resolved = (root / p).resolve()
    base = root.resolve()
    if base != resolved and base not in resolved.parents:
        raise ValueError(f"路径越出项目根: {rel}")
    return root / p


def contract_path(root: Path) -> Path:
    return root / ".receipt" / "contract.json"


def require_project(root: Path) -> None:
    if not contract_path(root).is_file():
        raise SystemExit(f"错误：{root} 不是有效验收项目（缺少 .receipt/contract.json），请先 init。")


def load_contract(root: Path) -> dict:
    return json.loads(read_text(contract_path(root)))


def save_contract(root: Path, data: dict) -> None:
    write_text(contract_path(root), json_dump(data), atomic=True)


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in read_text(path).splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def log_event(root: Path, event: str, **fields) -> None:
    append_jsonl(root / ".receipt" / "events.jsonl", {"at": now(), "event": event, **fields})


def esc(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# task rendering
# --------------------------------------------------------------------------

def render_task_md(contract: dict) -> str:
    lines = [
        f"# {contract.get('title', '未命名任务')}",
        "",
        "> 人可读视图。机器可读的唯一事实源是 `.receipt/contract.json`；两者冲突时以契约为准。",
        "",
        f"- 创建时间：{contract.get('created', '—')}",
        f"- 验收项：{len(contract.get('items', []))} 条",
        "- 判定规则：只有 `verify` 重新执行检查才产生 PASS；AI 自述只进 `claims.jsonl`。",
        "",
        "## 验收项",
        "",
        "| ID | 承诺 | 检查类型 | 参数 | 必需 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in contract.get("items", []):
        check = item.get("check", {})
        params = "、".join(f"{k}={v}" for k, v in check.items() if k != "type")
        lines.append(
            f"| {esc(item['id'])} | {esc(item['claim'])} | {esc(check.get('type', '—'))} | "
            f"{esc(params or '—')} | {'是' if item.get('required', True) else '否'} |"
        )
    lines += [
        "",
        "## 边界",
        "",
        "- 验收项必须可机器判定；「质量良好」「看起来没问题」这类表述不要写进来。",
        "- 契约变更（增删验收项、放宽阈值）会使旧验收单作废，需重跑 `verify`。",
        "- `command` 类型默认不执行，需显式 `--allow-commands` 授权。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------

def result(item: dict, status: str, evidence: str, detail: str = "", artifacts=None) -> dict:
    return {
        "id": item["id"],
        "claim": item["claim"],
        "required": item.get("required", True),
        "check": item.get("check", {}).get("type", "—"),
        "status": status,
        "evidence": evidence,
        "detail": detail,
        "artifacts": artifacts or [],
        "checked_at": now() if status != UNVERIFIED else None,
    }


def artifacts_for(root: Path, rel_paths: list[str]) -> list[dict]:
    out = []
    for rel in rel_paths:
        try:
            p = safe_rel(root, rel)
            if p.is_file():
                out.append({"path": rel, "sha256": sha256_file(p)})
        except ValueError:
            continue
    return out


def get_json_field(data, dotted: str):
    cur = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def check_item(root: Path, item: dict, allow_commands: bool) -> dict:
    check = item.get("check", {})
    kind = check.get("type")
    if kind not in CHECK_TYPES:
        return result(item, UNVERIFIED, f"未知检查类型 {kind}", "检查类型未实现，不能判定为通过")

    rel = check.get("path")
    try:
        p = safe_rel(root, rel) if rel else None
    except ValueError as exc:
        return result(item, FAIL, str(exc), "路径校验失败")

    try:
        if kind == "file_exists":
            ok = p is not None and p.is_file()
            return result(item, PASS if ok else FAIL,
                          f"{rel} " + ("存在" if ok else "不存在"),
                          artifacts=artifacts_for(root, [rel]) if ok else [])

        if kind in ("file_contains", "file_hash", "min_han", "min_lines", "json_equals"):
            if p is None or not p.is_file():
                return result(item, FAIL, f"{rel} 不存在", "缺少待检查产物")
            arts = artifacts_for(root, [rel])

            if kind == "file_contains":
                text = COMMENT_RE.sub("", read_text(p))
                if check.get("pattern"):
                    hit = bool(re.search(check["pattern"], text))
                    needle = f"正则 {check['pattern']}"
                else:
                    hit = str(check.get("text", "")) in text
                    needle = f"文本 {check.get('text', '')!r}"
                if check.get("absent"):
                    hit = not hit
                    needle = "不应出现 " + needle
                return result(item, PASS if hit else FAIL,
                              f"{rel}：{'命中' if hit else '未命中'} {needle}", artifacts=arts)

            if kind == "file_hash":
                actual = sha256_file(p)
                expected = str(check.get("hash", ""))
                ok = expected and actual.lower() == expected.lower()
                return result(item, PASS if ok else FAIL,
                              f"SHA-256 实测 {actual[:12]}… / 期望 {expected[:12] or '—'}…", artifacts=arts)

            if kind == "min_han":
                n = han_count(read_text(p))
                need = int(check.get("min", 1))
                return result(item, PASS if n >= need else FAIL,
                              f"汉字数 {n} / 要求 ≥ {need}（排除 frontmatter 与注释）", artifacts=arts)

            if kind == "min_lines":
                n = sum(1 for line in read_text(p).splitlines() if line.strip())
                need = int(check.get("min", 1))
                return result(item, PASS if n >= need else FAIL,
                              f"非空行数 {n} / 要求 ≥ {need}", artifacts=arts)

            if kind == "json_equals":
                data = json.loads(read_text(p))
                actual = get_json_field(data, str(check.get("key", "")))
                expected = check.get("value")
                ok = actual is not None and str(actual) == str(expected)
                return result(item, PASS if ok else FAIL,
                              f"{check.get('key')} 实测 {actual!r} / 期望 {expected!r}", artifacts=arts)

        if kind == "count_files":
            pattern = str(check.get("pattern", ""))
            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                return result(item, FAIL, f"不安全的 glob：{pattern}", "路径校验失败")
            found = [f for f in glob.glob(str(root / pattern), recursive=True) if Path(f).is_file()]
            n, need = len(found), int(check.get("min", 1))
            return result(item, PASS if n >= need else FAIL,
                          f"匹配 {n} 个文件 / 要求 ≥ {need}（{pattern}）",
                          artifacts=artifacts_for(root, [str(Path(f).relative_to(root)) for f in found[:20]]))

        if kind == "command":
            cmd = str(check.get("cmd", ""))
            if not allow_commands:
                return result(item, UNVERIFIED, f"未执行：{cmd}",
                              "command 类型需显式 --allow-commands 授权，且须先人工核对命令内容")
            if not cmd.strip():
                return result(item, UNVERIFIED, "空命令", "契约缺少 cmd")
            try:
                argv = shlex.split(cmd)
                proc = subprocess.run(argv, cwd=str(root), timeout=int(check.get("timeout", 60)),
                                      capture_output=True, text=True, encoding="utf-8", errors="replace")
                output = (proc.stdout or "") + (proc.stderr or "")
                contains = check.get("contains")
                ok = proc.returncode == 0 and (not contains or str(contains) in output)
                tail = output.strip().splitlines()[-3:]
                return result(item, PASS if ok else FAIL,
                              f"退出码 {proc.returncode}｜输出末行：{' / '.join(tail)[:160] or '（无输出）'}",
                              "命令经用户授权执行；不支持 shell 管道与 &&")
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                return result(item, FAIL, f"命令执行异常：{exc}", "检查执行阶段失败")
    except (OSError, ValueError, json.JSONDecodeError, re.error) as exc:
        return result(item, FAIL, f"检查异常：{exc}", "检查执行阶段失败")

    return result(item, UNVERIFIED, "未覆盖的分支", "无法判定")


def build_receipt(root: Path, allow_commands: bool) -> dict:
    contract = load_contract(root)
    results = [check_item(root, item, allow_commands) for item in contract.get("items", [])]
    counts = {s: sum(1 for r in results if r["status"] == s) for s in (PASS, FAIL, UNVERIFIED, STALE)}
    if counts[FAIL]:
        overall = "FAIL"
    elif counts[UNVERIFIED]:
        overall = "PARTIAL"
    elif not results:
        overall = "EMPTY"
    else:
        overall = "PASS"
    return {
        "format_version": FORMAT_VERSION,
        "title": contract.get("title", "未命名任务"),
        "verified_at": now(),
        "overall": overall,
        "counts": counts,
        "results": results,
        "note": "派生结果，可重建；冲突时以重新 verify 为准。AI 自述不参与判定。",
    }


def render_receipt_md(receipt: dict) -> str:
    lines = [
        f"# 验收单 · {receipt['title']}",
        "",
        f"- 复验时间：{receipt['verified_at']}",
        f"- 总体结论：**{receipt['overall']}**",
        f"- PASS {receipt['counts'][PASS]}｜FAIL {receipt['counts'][FAIL]}｜"
        f"UNVERIFIED {receipt['counts'][UNVERIFIED]}｜STALE {receipt['counts'][STALE]}",
        "- 判定口径：只有重新执行检查才产生 PASS；AI 自述（`claims.jsonl`）不参与判定。",
        "",
        "| ID | 承诺 | 结果 | 证据 | 复验时间 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in receipt["results"]:
        lines.append(f"| {esc(r['id'])} | {esc(r['claim'])} | **{r['status']}** | "
                     f"{esc(r['evidence'])} | {esc(r['checked_at'] or '—')} |")
    failed = [r for r in receipt["results"] if r["status"] in (FAIL, UNVERIFIED, STALE)]
    lines += ["", "## 未通过 / 待处理", ""]
    if failed:
        for r in failed:
            lines.append(f"- **{r['id']} · {r['status']}** {r['claim']}：{r['evidence']}"
                         + (f"（{r['detail']}）" if r["detail"] else ""))
    else:
        lines.append("- 无。")
    lines += ["", "> 本单只说明契约中的检查项是否通过，不等同于任务意图已满足；语义质量仍需人判断。", ""]
    return "\n".join(lines)


def write_receipt(root: Path, receipt: dict) -> None:
    write_text(root / ".receipt" / "receipt.json", json_dump(receipt), atomic=True)
    write_text(root / "RECEIPT.md", render_receipt_md(receipt), atomic=True)


def previous_artifact_hashes(root: Path) -> dict[str, dict]:
    p = root / ".receipt" / "receipt.json"
    if not p.is_file():
        return {}
    try:
        old = json.loads(read_text(p))
    except json.JSONDecodeError:
        return {}
    return {r["id"]: {a["path"]: a["sha256"] for a in r.get("artifacts", [])}
            for r in old.get("results", [])}


def mark_stale(receipt: dict, old_hashes: dict) -> int:
    changed = 0
    for r in receipt["results"]:
        prior = old_hashes.get(r["id"])
        if not prior or r["status"] in (FAIL, UNVERIFIED):
            continue
        current = {a["path"]: a["sha256"] for a in r.get("artifacts", [])}
        if prior != current:
            r["status"] = STALE
            r["detail"] = "产物在上次验收后被改动，旧证据失效，须重新验收"
            r["evidence"] += "｜[证据已过期]"
            changed += 1
    if changed:
        counts = {s: sum(1 for r in receipt["results"] if r["status"] == s)
                  for s in (PASS, FAIL, UNVERIFIED, STALE)}
        receipt["counts"] = counts
        receipt["overall"] = "STALE"
    return changed


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_init(args) -> int:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if contract_path(root).exists() and not args.force:
        raise SystemExit(f"错误：{root} 已有验收契约。确需重置请加 --force（不删除事件日志）。")
    contract = {
        "format_version": FORMAT_VERSION,
        "title": args.title,
        "created": now(),
        "items": [],
    }
    save_contract(root, contract)
    write_text(root / "TASK.md", render_task_md(contract))
    for name in ("claims.jsonl", "events.jsonl"):
        p = root / ".receipt" / name
        if not p.exists():
            write_text(p, "")
    log_event(root, "init", title=args.title)
    print(f"已创建验收项目：{root}")
    print(f"任务：{args.title}")
    print("下一步：用 add 登记可机器判定的验收项，并与人确认 TASK.md。")
    return 0


def cmd_add(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    contract = load_contract(root)
    if any(i["id"] == args.id for i in contract["items"]):
        raise SystemExit(f"错误：验收项 {args.id} 已存在。")
    check: dict = {"type": args.check}
    for key in ("path", "text", "pattern", "hash", "key", "value", "cmd", "contains"):
        value = getattr(args, key, None)
        if value is not None and value != "":
            check[key] = value
    if args.min is not None:
        check["min"] = args.min
    if args.absent:
        check["absent"] = True
    if args.timeout is not None:
        check["timeout"] = args.timeout

    needs_path = args.check in ("file_exists", "file_contains", "file_hash", "min_han", "min_lines", "json_equals")
    if needs_path and "path" not in check:
        raise SystemExit(f"错误：{args.check} 需要 --path。")
    if args.check == "file_contains" and not any(k in check for k in ("text", "pattern")):
        raise SystemExit("错误：file_contains 需要 --text 或 --pattern。")
    if args.check == "file_hash" and "hash" not in check:
        raise SystemExit("错误：file_hash 需要 --hash。")
    if args.check == "json_equals" and "key" not in check:
        raise SystemExit("错误：json_equals 需要 --key。")
    if args.check == "count_files" and "pattern" not in check:
        raise SystemExit("错误：count_files 需要 --pattern（glob）。")
    if args.check == "command" and "cmd" not in check:
        raise SystemExit("错误：command 需要 --cmd。")

    contract["items"].append({
        "id": args.id,
        "claim": args.claim,
        "required": not args.optional,
        "check": check,
    })
    save_contract(root, contract)
    write_text(root / "TASK.md", render_task_md(contract))
    log_event(root, "add", id=args.id, check=args.check)
    print(f"已登记验收项 {args.id}：{args.claim}")
    print("提醒：契约变更后旧验收单作废，需重跑 verify。")
    return 0


def cmd_verify(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    receipt = build_receipt(root, args.allow_commands)
    write_receipt(root, receipt)
    log_event(root, "verify", overall=receipt["overall"], counts=receipt["counts"])
    if args.json:
        print(json_dump(receipt), end="")
    else:
        print(f"验收单 · {receipt['title']}｜结论：{receipt['overall']}｜{receipt['verified_at']}")
        for r in receipt["results"]:
            print(f"  [{r['status']:<10}] {r['id']} {r['claim']}")
            print(f"              {r['evidence']}")
        print(f"PASS {receipt['counts'][PASS]}｜FAIL {receipt['counts'][FAIL]}｜"
              f"UNVERIFIED {receipt['counts'][UNVERIFIED]}")
        print(f"已写出：{root / 'RECEIPT.md'}")
        if receipt["counts"][UNVERIFIED]:
            print("注意：UNVERIFIED 不是通过；command 类型需 --allow-commands 授权。")
    return 1 if receipt["counts"][FAIL] else 0


def cmd_attest(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    append_jsonl(root / ".receipt" / "claims.jsonl", {
        "at": now(), "id": args.id, "note": args.note,
        "weight": "声明，不参与验收判定",
    })
    log_event(root, "attest", id=args.id)
    print(f"已记录声明 {args.id}（仅留痕，不产生 PASS）。")
    print("复验请运行 verify；声明与证据的差距可在 history 中回看。")
    return 0


def cmd_handoff(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    contract = load_contract(root)
    p = root / ".receipt" / "receipt.json"
    receipt = json.loads(read_text(p)) if p.is_file() else None
    claims = read_jsonl(root / ".receipt" / "claims.jsonl")
    stale = []
    if receipt:
        old = previous_artifact_hashes(root)
        for r in receipt["results"]:
            if r["status"] not in (PASS, FAIL, UNVERIFIED):
                continue
            prior = old.get(r["id"], {})
            current = {a["path"]: a["sha256"] for a in r.get("artifacts", [])}
            if prior and current and prior != current:
                stale.append(r["id"])

    lines = [
        f"# 交接包 · {contract['title']}",
        "",
        f"- 生成时间：{now()}",
        f"- 事实源：`.receipt/contract.json`（{len(contract['items'])} 条验收项）",
        "- 本文件自包含：不依赖聊天记录、不依赖任何特定 AI 工具。",
        "",
        "## 当前状态",
        "",
    ]
    if receipt:
        lines += [
            f"- 上次复验：{receipt['verified_at']}｜结论 **{receipt['overall']}**",
            f"- PASS {receipt['counts'][PASS]}｜FAIL {receipt['counts'][FAIL]}｜UNVERIFIED {receipt['counts'][UNVERIFIED]}",
            "",
            "| ID | 承诺 | 结果 | 证据 |",
            "| --- | --- | --- | --- |",
        ]
        for r in receipt["results"]:
            lines.append(f"| {esc(r['id'])} | {esc(r['claim'])} | {r['status']} | {esc(r['evidence'])} |")
    else:
        lines.append("- 尚未执行过 `verify`，没有任何可引用的验收结论。")
    lines += ["", "## 证据有效性", ""]
    lines.append("交接时检测到证据过期的验收项：" + ("、".join(stale) if stale else "无")
                 if receipt else "未验收，无从判断。")
    lines += ["", "## 未决事项", ""]
    pending = [r for r in (receipt or {}).get("results", []) if r["status"] != PASS]
    if pending:
        for r in pending:
            lines.append(f"- {r['id']}（{r['status']}）：{r['claim']} — {r['evidence']}")
    else:
        lines.append("- 无（仅指契约内检查项，不代表任务意图已满足）。")
    lines += [
        "",
        "## AI 自述声明（未经验收，仅供参考）",
        "",
    ]
    if claims:
        for c in claims[-10:]:
            lines.append(f"- `{c['at']}` {c['id']}：{c['note']}")
    else:
        lines.append("- 无。")
    lines += [
        "",
        "## 下一步",
        "",
        args.next or "（未填写）",
        "",
        "## 给下一个助手的指令",
        "",
        "1. 先读完本文件，再读 `TASK.md` 与 `.receipt/contract.json`。",
        "2. 运行 `python -X utf8 <receipt.py> resume --root <项目根>` 重新复验；不要继承这里的 PASS 结论。",
        "3. 对 FAIL / UNVERIFIED / STALE 逐项处理，处理后再 `verify`。",
        "4. 不要在未重新复验的情况下宣布任务完成；自述用 `attest` 记录即可。",
        "",
        "## 文件清单",
        "",
        "- `TASK.md` 任务契约（人读）｜`RECEIPT.md` 验收单｜`.receipt/contract.json` 契约（事实源）",
        "- `.receipt/claims.jsonl` 自述声明｜`.receipt/events.jsonl` 事件日志",
        "",
    ]
    write_text(root / "HANDOFF.md", "\n".join(lines), atomic=True)
    log_event(root, "handoff", next=args.next)
    print(f"已生成交接包：{root / 'HANDOFF.md'}")
    print(f"验收项 {len(contract['items'])} 条｜证据过期 {len(stale)} 条｜自述声明 {len(claims)} 条")
    print("把 HANDOFF.md 贴给任何 AI 助手或同事即可继续，不需要聊天上下文。")
    return 0


def cmd_resume(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    handoff = root / "HANDOFF.md"
    if not handoff.is_file():
        print("提示：未找到 HANDOFF.md，将直接按当前契约重新复验。")
    old_hashes = previous_artifact_hashes(root)
    receipt = build_receipt(root, args.allow_commands)
    changed = mark_stale(receipt, old_hashes)
    write_receipt(root, receipt)
    log_event(root, "resume", overall=receipt["overall"], stale=changed)
    print(f"恢复 · {receipt['title']}｜结论：{receipt['overall']}")
    print("注意：所有结论均由本次重新复验得出，未继承历史 PASS。")
    for r in receipt["results"]:
        flag = "  <-- 证据已过期" if r["status"] == STALE else ""
        print(f"  [{r['status']:<10}] {r['id']} {r['claim']}{flag}")
    if changed:
        print(f"{changed} 项产物在上次验收后被改动，须重新验收。")
    print("下一步：处理 FAIL / UNVERIFIED / STALE，再运行 verify。")
    return 1 if receipt["counts"][FAIL] or changed else 0


def cmd_history(args) -> int:
    root = Path(args.root).resolve()
    require_project(root)
    events = read_jsonl(root / ".receipt" / "events.jsonl")
    claims = read_jsonl(root / ".receipt" / "claims.jsonl")
    print(f"事件 {len(events)} 条｜声明 {len(claims)} 条（声明不参与判定）")
    for e in events[-args.limit:]:
        extra = " ".join(f"{k}={v}" for k, v in e.items() if k not in ("at", "event"))
        print(f"  {e['at']} {e['event']:<8} {extra[:160]}")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", required=True, help="项目根目录")
    common.add_argument("--json", action="store_true", help="输出机器可读 JSON（verify 支持）")
    common.add_argument("--allow-commands", action="store_true",
                        help="显式授权执行契约中的 command 检查；默认拒绝")

    p = argparse.ArgumentParser(description="Agent Receipt · AI 任务可复验验收与交接（仅标准库）")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("init", parents=[common], help="创建验收契约")
    q.add_argument("--title", required=True)
    q.add_argument("--force", action="store_true")
    q.set_defaults(func=cmd_init)

    q = sub.add_parser("add", parents=[common], help="登记验收项")
    q.add_argument("--id", required=True)
    q.add_argument("--claim", required=True)
    q.add_argument("--check", required=True, choices=CHECK_TYPES)
    q.add_argument("--path", help="待检查文件（项目内相对路径）")
    q.add_argument("--text", help="需包含/排除的文本")
    q.add_argument("--pattern", help="正则，或 count_files 的 glob")
    q.add_argument("--hash", dest="hash", help="期望的 SHA-256")
    q.add_argument("--key", help="json_equals 的字段路径，如 a.b")
    q.add_argument("--value", help="json_equals 的期望值")
    q.add_argument("--cmd", help="command 类型要执行的命令")
    q.add_argument("--contains", help="command 输出需包含的文本")
    q.add_argument("--min", type=int, help="最小数量/汉字数/行数")
    q.add_argument("--timeout", type=int, help="命令超时秒数")
    q.add_argument("--absent", action="store_true", help="file_contains 反转为「不应出现」")
    q.add_argument("--optional", action="store_true", help="标记为非必需验收项")
    q.set_defaults(func=cmd_add)

    q = sub.add_parser("verify", parents=[common], help="逐条重新复验")
    q.set_defaults(func=cmd_verify)

    q = sub.add_parser("attest", parents=[common], help="记录 AI 自述（不产生 PASS）")
    q.add_argument("--id", required=True)
    q.add_argument("--note", required=True)
    q.set_defaults(func=cmd_attest)

    q = sub.add_parser("handoff", parents=[common], help="生成自包含交接包")
    q.add_argument("--next", default="")
    q.set_defaults(func=cmd_handoff)

    q = sub.add_parser("resume", parents=[common], help="从交接包恢复并重新复验")
    q.set_defaults(func=cmd_resume)

    q = sub.add_parser("history", parents=[common], help="查看事件与声明日志")
    q.add_argument("--limit", type=int, default=20)
    q.set_defaults(func=cmd_history)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3
    except (OSError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
