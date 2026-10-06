#!/usr/bin/env python
"""备课 / 讲课取资料 / 学生画像 / 诊断 / 进度 —— 整条链路的端到端测试。

跑法（在 agent_studio 目录下）：

    py -3.14 tests/test_course_flow.py              # 完整链路（备课约 1~3 分钟）
    py -3.14 tests/test_course_flow.py --topic "酸碱盐"
    py -3.14 tests/test_course_flow.py --skip-prep  # 跳过备课，只测其余工具（用已有课程）

为什么写成脚本而不是靠模型跑一遍：
备课要调用一次大模型、花几分钟，而且结果有随机性。**先把管道测通**（工具本身对不对、
文件有没有落到该落的地方、画像数据形状对不对），再去测模型会不会用 —— 顺序反了的话，
出了问题分不清是工具坏了还是模型没调对。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.registry import ToolContext, get                       # noqa: E402
from kernel.session import STORE                                   # noqa: E402
from kernel.tools import load_builtin_tools                        # noqa: E402

PASS, FAIL = "  ✓", "  ✗"
_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(f"{PASS if ok else FAIL} {name}" + (f"  →  {detail}" if detail else ""))


async def call(tool_name: str, ctx: ToolContext, args: dict):
    tool = get(tool_name)
    if tool is None:
        check(f"{tool_name} 已注册", False, "工具不存在")
        return None
    result = await tool.call(ctx, args)
    print(f"\n───── {tool_name} ─────")
    print(f"  ok={result.ok}")
    print("  " + (result.summary or "").replace("\n", "\n  ")[:1400])
    if result.ui:
        types = [i.get("type") for i in (result.ui.get("items") or [])]
        print(f"  UI 组件: {types}")
    return result


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", default="Python 列表与字典")
    parser.add_argument("--skip-prep", action="store_true")
    parser.add_argument("--learner", default="flow_test", help="测试用的学生标识")
    args = parser.parse_args()

    load_builtin_tools()
    session = STORE.get_or_create("course_flow_test", username=args.learner)
    ctx = ToolContext(session=session, call_id="test", username=args.learner)

    folder = session.lesson_folder

    # 1) 备课
    if not args.skip_prep:
        print(f"\n===== 1/6 备课：{args.topic}（要等 1~3 分钟）=====")
        result = await call("prepare_course", ctx, {"topic": args.topic, "level": "零基础"})
        if not result or not result.ok:
            print("\n备课失败，后面的链路无法继续。")
            return 1
        folder = (result.data or {}).get("folder")
    else:
        print("\n===== 1/6 跳过备课 =====")

    check("会话已绑定课程", bool(folder), str(folder))
    if not folder:
        return 1

    lesson_dir = ROOT / "data" / "lessons" / folder
    print(f"\n===== 2/6 落盘文件检查：{lesson_dir} =====")
    for name in ("config.json", "syllabus.json", "progress.json", "knowledge_graph.json"):
        check(name, (lesson_dir / name).exists())
    sections = sorted(p.name for p in lesson_dir.glob("section_*") if p.is_dir())
    check("section_N 单元目录", len(sections) > 0, f"{len(sections)} 个: {sections[:5]}")
    unit_md = lesson_dir / "section_1" / "unit.md"
    check("第 1 课资料正文 section_1/unit.md", unit_md.exists(),
          f"{unit_md.stat().st_size} 字节" if unit_md.exists() else "")

    syllabus = json.loads((lesson_dir / "syllabus.json").read_text(encoding="utf-8"))
    units = syllabus.get("units") or []
    check("教案里有单元", len(units) >= 1, f"{len(units)} 课")
    quiz_total = len(syllabus.get("quiz_preset") or [])
    print(f"  课程目标: {str(syllabus.get('course_target'))[:80]}")
    print(f"  随堂测验题: {quiz_total} 道")

    # 3) 讲课取资料（这是每节课都会用到的）
    print("\n===== 3/6 讲课取资料 =====")
    await call("lesson_context", ctx, {"part": "outline"})
    unit_ctx = await call("lesson_context", ctx, {"part": "unit", "unit_index": 1})
    check("能取到第 1 课教案正文", bool(unit_ctx and unit_ctx.ok))

    # 4) 记录答题（学生画像的唯一入口）
    print("\n===== 4/6 记录答题 → 学生画像 =====")
    wrong = await call("record_result", ctx, {
        "concept": units[0].get("key_points", ["知识点"])[0] if units and units[0].get("key_points") else "基础概念",
        "correct": False, "note": "把索引当成了从 1 开始数",
    })
    check("答错被记录", bool(wrong and wrong.ok))
    right = await call("record_result", ctx, {"concept": "基础概念", "correct": True})
    check("答对被记录", bool(right and right.ok))

    learner_file = ROOT / "data" / "lessons" / "learners" / f"learner_{args.learner}.json"
    check("学生档案文件已生成", learner_file.exists(), str(learner_file.name))
    if learner_file.exists():
        learner = json.loads(learner_file.read_text(encoding="utf-8"))
        ks = learner.get("knowledge_state") or {}
        errors = learner.get("error_memory") or []
        check("档案里有掌握度记录", len(ks) > 0, f"{len(ks)} 个知识点")
        check("档案里有错题记忆", len(errors) > 0, f"{len(errors)} 条")
        print(f"  掌握度: {json.dumps(ks, ensure_ascii=False)[:200]}")
        print(f"  错题: {json.dumps(errors[-1], ensure_ascii=False)[:200] if errors else '无'}")

    # 5) 画像 / 诊断 / 下一步
    print("\n===== 5/6 画像 · 诊断 · 下一步 =====")
    await call("learner_status", ctx, {})
    await call("diagnose", ctx, {})
    await call("plan_next", ctx, {})

    # 6) 进度
    print("\n===== 6/6 学习进度 =====")
    await call("course_progress", ctx, {"unit_index": 1, "completed": True, "quiz_score": 80})
    progress = json.loads((lesson_dir / "progress.json").read_text(encoding="utf-8"))
    check("进度已更新", progress.get("current_unit") == 2,
          f"当前第 {progress.get('current_unit')} 课，已完成 {progress.get('completed_units')}")
    await call("list_courses", ctx, {})

    print("\n" + "=" * 60)
    print("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗")
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
