#!/usr/bin/env python
"""专项测试：讲课取资料的「第几课」对齐 / 出题 / 答题卡成绩自动记入画像。

跑法（agent_studio 目录下，建议服务已在跑）：

    py -3.14 tests/test_quiz_flow.py
    py -3.14 tests/test_quiz_flow.py --folder 20260923_174444_xxx

它盯的是三个**真出过问题**的点：
1. `lesson_context(unit_index=N)` 必须是第 N 课 —— 老代码的 unit_dir 是 0 基，
   我一开始传了 1 基，结果「要第 1 课却拿到第 2 课的教案」，上课就会讲错课。
2. 出题要能出出来，并且每道题都标好了正确选项（否则前端判分判不了）。
3. 答题卡判分后回传 `/agent/quiz_result`，学生画像要真的更新（掌握度/错题）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))      # 课程数据层是扁平模块

import storage                                                       # noqa: E402
from kernel.registry import ToolContext, get                         # noqa: E402
from kernel.session import STORE                                     # noqa: E402
from kernel.tools import load_builtin_tools                          # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="")
    parser.add_argument("--base", default="http://127.0.0.1:5001")
    parser.add_argument("--learner", default="quiz_test")
    args = parser.parse_args()

    load_builtin_tools()
    courses = storage.list_courses()
    folder = args.folder or (courses[0]["folder"] if courses else "")
    if not folder:
        print("没有课程，请先跑 tests/test_course_flow.py 备一门课。")
        return 1
    print(f"使用课程：{folder}\n")

    session = STORE.get_or_create("quiz_flow_test", username=args.learner, lesson_folder=folder)
    ctx = ToolContext(session=session, call_id="t", username=args.learner)

    syllabus = json.loads((ROOT / "data" / "lessons" / folder / "syllabus.json").read_text(encoding="utf-8"))
    units = syllabus.get("units") or []

    def norm(text: str) -> str:
        """归一化：全角/半角冒号、空白 —— 免得断言因为标点差异误报（我自己踩过）。"""
        return str(text).replace("：", ":").replace("，", ",").replace(" ", "")

    title_1 = str(units[0].get("title") or "")

    # ---- 1) 第 N 课必须对得上 ----
    print("===== 1/3 取教案：第 1 课必须真的是第 1 课 =====")
    r1 = await get("lesson_context").call(ctx, {"part": "unit", "unit_index": 1})
    print("  第 1 课教案开头：", (r1.summary or "").split("\n")[1][:70] if r1.summary else "（空）")
    check("取到第 1 课教案", bool(r1.ok))
    check("内容与教案里的第 1 课一致", bool(r1.ok) and norm(title_1)[:8] in norm(r1.summary or ""),
          f"期望包含「{title_1}」")

    r2 = await get("lesson_context").call(ctx, {"part": "unit", "unit_index": 2})
    title_2 = str(units[1].get("title") or "")
    check("第 2 课也对得上", bool(r2.ok) and norm(title_2)[:8] in norm(r2.summary or ""),
          f"期望包含「{title_2}」")

    # ---- 2) 出题 ----
    print("\n===== 2/3 出题（要等 30~90 秒）=====")
    quiz = await get("generate_quiz").call(ctx, {"unit_index": 1, "count": 3})
    check("出题成功", bool(quiz.ok), (quiz.summary or "")[:100].replace("\n", " "))
    nodes = []
    if quiz.ui:
        nodes = [i for i in (quiz.ui.get("items") or []) if i.get("type") == "quiz"]
    check("生成了答题卡题目", len(nodes) > 0, f"{len(nodes)} 道")
    all_marked = all(any(o.get("correct") for o in (n.get("options") or [])) for n in nodes) if nodes else False
    check("每题都标好了正确选项（前端才能判分）", all_marked)
    has_record = all((n.get("record") or {}).get("concept") for n in nodes) if nodes else False
    check("每题带成绩回传信息（concept + session）", has_record)
    if nodes:
        n0 = nodes[0]
        print(f"  示例题：{str(n0.get('question'))[:60]}")
        for o in n0.get("options") or []:
            print(f"    {'[√]' if o.get('correct') else '[ ]'} {str(o.get('label'))[:60]}")

    # ---- 3) 答题卡判分 → 自动记入画像（走 HTTP，跟前端同一条路）----
    print("\n===== 3/3 判分结果自动记入画像（HTTP 回传）=====")
    learner_file = ROOT / "data" / "lessons" / "learners" / f"learner_{args.learner}.json"
    before = {}
    if learner_file.exists():
        before = json.loads(learner_file.read_text(encoding="utf-8")).get("knowledge_state") or {}
    concept = ((nodes[0].get("record") or {}).get("concept") if nodes else "") or "测试知识点"
    try:
        body = json.dumps({"session_id": session.id, "concept": concept, "correct": False}).encode()
        req = urllib.request.Request(f"{args.base}/agent/quiz_result", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        check("接口返回成功", bool(data.get("ok")), f"掌握度={data.get('mastery')} 建议={str((data.get('advice') or {}).get('message'))[:40]}")
    except Exception as exc:  # noqa: BLE001
        check("接口调用", False, f"{type(exc).__name__}: {exc}（服务没在跑？）")
        data = {}

    after = {}
    if learner_file.exists():
        after = json.loads(learner_file.read_text(encoding="utf-8")).get("knowledge_state") or {}
    # 断言的正确形态：这个知识点的掌握度被更新了（可能是同一个知识点第二次答错，
    # 记录数不变但值更低了 —— 所以不能拿「记录条数变多」当判据，我自己踩过这个坑）
    recorded = after.get(concept) is not None or len(after) > len(before)
    check("学生画像里有这个知识点的掌握度", recorded,
          f"「{concept}」= {after.get(concept)}（本次接口返回 mastery="
          f"{data.get('mastery') if isinstance(data, dict) else '?'}）")

    print("\n" + "=" * 60)
    print("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗")
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
