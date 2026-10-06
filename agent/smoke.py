#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""内核冒烟测试 / 命令行对话（不开 HTTP 服务，直接跑 AgentRuntime）。

用法：
    py -3.14 agent/smoke.py                          # 只做自检：链路 + 工具清单
    py -3.14 agent/smoke.py "帮我算 (12.5*3+7)/2"     # 真跑一轮，看它会不会调工具
    py -3.14 agent/smoke.py --session demo "讲一下酸碱盐" --show-ui

它把事件流按人话打出来，是「内核到底干了什么」最直接的观察窗口——
出问题时先跑它，比在前端翻半天 console 快得多。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import events                                    # noqa: E402
from agent.config import llm_configs                        # noqa: E402
from agent.llm import probe_chain                           # noqa: E402
from agent.plugins import list_plugins, load_plugins        # noqa: E402
from agent.registry import describe_tools                   # noqa: E402
from agent.runtime import AgentRuntime                      # noqa: E402
from agent.session import STORE                             # noqa: E402

C_DIM = "\033[2m"
C_TOOL = "\033[36m"
C_OK = "\033[32m"
C_ERR = "\033[31m"
C_UI = "\033[35m"
C_END = "\033[0m"


def banner(text: str) -> None:
    print(f"\n{C_DIM}{'─' * 8} {text} {'─' * 8}{C_END}")


async def selfcheck() -> None:
    from agent.tools import load_builtin_tools

    load_builtin_tools()          # 导入即注册内置工具（AgentRuntime 也会做，这里为了先看到清单）
    banner("模型链路")
    for item in await probe_chain(llm_configs()):
        mark = f"{C_OK}✓{C_END}" if item["ok"] else f"{C_ERR}✗{C_END}"
        detail = f"  {C_DIM}{item['detail']}{C_END}" if item["detail"] else ""
        print(f"  {mark} {item['label']}{detail}")
    load_plugins(verbose=True)
    banner("已注册工具")
    for t in describe_tools():
        print(f"  {C_TOOL}{t['name']}{C_END}  {C_DIM}[{t['source']}]{C_END}  {t['description'][:70]}")
    plugins = list_plugins()
    if plugins:
        banner("插件")
        for p in plugins:
            state = f"{C_OK}已加载{C_END}" if p["loaded"] else f"{C_ERR}{p['error']}{C_END}"
            print(f"  {p['name']} v{p['version']} → {state}  工具={p['tools']}")


async def run_once(prompt: str, session_id: str, show_ui: bool, lesson: str = "") -> int:
    session = STORE.get_or_create(session_id, lesson_folder=lesson or None)
    runtime = AgentRuntime()
    tool_calls = 0
    ui_count = 0

    print(f"\n{C_DIM}会话 {session.id}｜工作目录 {session.workspace}{C_END}")
    print(f"{C_DIM}学生：{prompt}{C_END}\n")
    print("老师：", end="", flush=True)

    async for event in runtime.run(session, prompt):
        if event.type == events.TEXT:
            print(event.data.get("delta") or "", end="", flush=True)
        elif event.type == events.THINKING:
            continue
        elif event.type == events.TOOL_CALL:
            tool_calls += 1
            args = json.dumps(event.data.get("args") or {}, ensure_ascii=False)
            print(f"\n{C_TOOL}  ⚙ 调用 {event.data['name']} {args[:300]}{C_END}")
        elif event.type == events.TOOL_RESULT:
            flag = f"{C_OK}ok{C_END}" if event.data.get("ok") else f"{C_ERR}失败{C_END}"
            ms = event.data.get("ms", 0)
            summary = (event.data.get("summary") or "").replace("\n", " ⏎ ")[:220]
            print(f"{C_TOOL}  ↳ {flag} {ms}ms  {summary}{C_END}", flush=True)
        elif event.type == events.UI:
            ui_count += 1
            spec = event.data.get("spec") or {}
            if show_ui:
                print(f"\n{C_UI}  ▣ UI 规格：{json.dumps(spec, ensure_ascii=False)[:1200]}{C_END}")
            else:
                print(f"{C_UI}  ▣ 渲染界面（{len(json.dumps(spec, ensure_ascii=False))} 字节）{C_END}", flush=True)
        elif event.type == events.ARTIFACT:
            print(f"{C_UI}  ▤ 产物 {event.data.get('path')} ({event.data.get('bytes')} 字节){C_END}", flush=True)
        elif event.type == events.NOTICE:
            print(f"\n{C_DIM}  ⓘ {event.data.get('message')}{C_END}", flush=True)
        elif event.type == events.ERROR:
            print(f"\n{C_ERR}  ✗ {event.data.get('message')}{C_END}")
            detail = event.data.get("detail")
            if detail:
                print(f"{C_DIM}{detail[-800:]}{C_END}")
        elif event.type == events.RUN_END:
            reason = event.data.get("reason")
            print(
                f"\n\n{C_DIM}—— 结束：{reason}｜{event.data.get('steps')} 步｜"
                f"{event.data.get('ms')}ms｜工具调用 {tool_calls} 次｜界面 {ui_count} 个 ——{C_END}"
            )

    files = sorted(p.name for p in session.workspace.iterdir() if p.is_file())
    if files:
        print(f"{C_DIM}工作目录文件：{', '.join(files)}{C_END}")
    return 0 if tool_calls or ui_count or True else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 内核冒烟测试")
    parser.add_argument("prompt", nargs="?", default="", help="学生提问；留空则只做自检")
    parser.add_argument("--session", default="smoke", help="会话 id（复用可保留上下文）")
    parser.add_argument("--lesson", default="", help="课程目录名（可选）")
    parser.add_argument("--show-ui", action="store_true", help="打印完整 UI 规格")
    parser.add_argument("--new", action="store_true", help="开一个新会话（不复用历史）")
    args = parser.parse_args()

    async def main_async() -> int:
        if not args.prompt:
            await selfcheck()
            return 0
        session_id = args.session
        if args.new:
            import uuid

            session_id = uuid.uuid4().hex[:10]
        await selfcheck()
        banner("开始对话")
        return await run_once(args.prompt, session_id, args.show_ui, args.lesson)

    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
