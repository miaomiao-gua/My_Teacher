#!/usr/bin/env python
"""对话自动归档自测：**纯聊天，AI 自己判断该放进哪个文件夹**。

两条硬规则必须验（都是「别帮倒忙」）：
  1. 用户自己归过档的，自动归档不许覆盖；
  2. 模型判断不出/置信度低 → 保持原样（宁可留在「未分类」，也不要塞错地方）。

还要验：模型输出带 markdown 围栏/废话时也解析得出来；判断依据取对话**开头**（学科在开头就定了）。

跑法：py -3.14 tests/test_filing.py            # 纯逻辑（不联网）
      py -3.14 tests/test_filing.py --live     # 额外跑一次真实模型判断
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel import filing                                    # noqa: E402
from kernel.session import STORE                             # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def make_session(sid: str, turns: int = 3):
    sess = STORE.get_or_create(sid, username="filing_test")
    sess.subject = ""
    sess.textbook = ""
    sess.title = ""
    sess.meta = {}
    sess.messages = []
    for i in range(turns):
        sess.add_user(f"我在学 9709 的三角函数，第 {i} 个问题：sin 的图像怎么画？")
        sess.add_assistant("我们先看单位圆……")
    return sess


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="额外跑一次真实模型判断")
    args = ap.parse_args()

    print("===== 1) 解析模型输出（模型经常带围栏和废话）=====")
    ok_json = '{"subject":"数学","textbook":"9709","confidence":0.9,"reason":"提到 A-level 数学"}'
    check("干净 JSON", (filing.parse_filing(ok_json) or {}).get("subject") == "数学")
    fenced = "好的，我的判断是：\n```json\n" + ok_json + "\n```\n希望有帮助。"
    check("带 markdown 围栏", (filing.parse_filing(fenced) or {}).get("subject") == "数学")
    check("带前后废话", (filing.parse_filing("综上 " + ok_json + " 以上") or {}).get("subject") == "数学")
    check("完全不合法 → None", filing.parse_filing("我判断不出来") is None)
    check("空 → None", filing.parse_filing("") is None)
    check("学科是「未分类」→ 视为没判断出来",
          filing.parse_filing('{"subject":"未分类","textbook":"x"}') is None)
    check("缺教材 → 补「未指定教材」",
          (filing.parse_filing('{"subject":"物理"}') or {}).get("textbook") == "未指定教材")
    check("confidence 乱写也不炸",
          0 <= (filing.parse_filing('{"subject":"化学","confidence":"很高"}') or {}).get("confidence", -1) <= 1)
    check("名字太长会被截断",
          len((filing.parse_filing('{"subject":"' + "数" * 80 + '"}') or {}).get("subject", "")) <= filing.MAX_SUBJECT)
    check("斜杠被换掉（免得和路径分隔符混淆）",
          "/" not in (filing.parse_filing('{"subject":"数学/物理"}') or {}).get("subject", "x"))

    print("\n===== 2) 判断依据取对话开头 =====")
    sess = make_session("filing_excerpt", turns=5)
    excerpt = filing.conversation_excerpt(sess, max_messages=4)
    check("包含了开头", "三角函数" in excerpt)
    check("不会把整段对话都塞进去", len(excerpt) <= 1200, f"{len(excerpt)} 字")
    check("标记了谁在说话", "学生：" in excerpt and "老师：" in excerpt)

    print("\n===== 3) 什么时候该判断（needs_filing）=====")
    s1 = make_session("filing_needs1")
    check("未分类 + 有对话 → 该判断", filing.needs_filing(s1) is True)
    s2 = make_session("filing_needs2")
    s2.subject = "数学"
    check("已经有学科 → 不判断", filing.needs_filing(s2) is False)
    s3 = make_session("filing_needs3")
    s3.meta = {"filed_by": "user"}
    check("用户自己归过档 → 不判断", filing.needs_filing(s3) is False)
    s4 = STORE.get_or_create("filing_needs4", username="filing_test")
    s4.subject = ""
    s4.meta = {}
    s4.messages = []
    check("一句话都没说 → 不判断", filing.needs_filing(s4) is False)
    s5 = make_session("filing_needs5")
    s5.meta = {"filing_tried_at": time.time()}
    check("刚试过（冷却期内）→ 不重复问模型", filing.needs_filing(s5) is False)

    print("\n===== 4) 落盘与两条硬规则 =====")
    s6 = make_session("filing_apply")
    changed = filing.apply_filing(s6, "数学", "9709", by="model", reason="提到 A-level")
    check("写进了学科/教材", s6.subject == "数学" and s6.textbook == "9709" and changed is True)
    check("记了是谁归的档", (s6.meta or {}).get("filed_by") == "model")
    check("重复归档 → changed=False", filing.apply_filing(s6, "数学", "9709") is False)
    # 规则 1：用户手动定过 → 模型不能覆盖
    s7 = make_session("filing_userrule")
    filing.apply_filing(s7, "语文", "古文观止", by="user")
    blocked = filing.apply_filing(s7, "数学", "9709", by="model")
    check("用户定过的，模型覆盖不了", blocked is False and s7.subject == "语文", s7.subject)
    # 但用户自己可以改
    filing.apply_filing(s7, "数学", "9709", by="user")
    check("用户自己可以再改", s7.subject == "数学")
    # 规则 2：没给出学科 → 不动
    s8 = make_session("filing_empty")
    check("不给学科 → 不动", filing.apply_filing(s8, "") is False and s8.subject == "")
    check("写完之后磁盘上有", (ROOT / "data" / "sessions" / "filing_apply.json").is_file())
    check("文件夹被登记了（空文件夹也能留在左栏）",
          any(f["subject"] == "数学" and f["textbook"] == "9709" for f in filing.existing_folders()))

    print("\n===== 5) 后台兜底：置信度低就不动 =====")

    async def low_conf():
        sess = make_session("filing_lowconf")
        orig = filing.classify_session

        async def fake(_s, **_kw):
            return {"subject": "数学", "textbook": "9709", "confidence": 0.2, "reason": "猜的"}

        filing.classify_session = fake                      # type: ignore[assignment]
        try:
            info = await filing.maybe_autofile(sess)
        finally:
            filing.classify_session = orig                  # type: ignore[assignment]
        return sess, info

    sess, info = asyncio.run(low_conf())
    check("置信度低于阈值 → 不归（留在未分类）", info is None and sess.subject == "", sess.subject)
    check("但记下了「试过了」，不会反复问", bool((sess.meta or {}).get("filing_tried_at")))

    async def good_conf():
        sess = make_session("filing_goodconf")
        orig = filing.classify_session

        async def fake(_s, **_kw):
            return {"subject": "物理", "textbook": "人教版必修一", "confidence": 0.88, "reason": "讲受力分析"}

        filing.classify_session = fake                      # type: ignore[assignment]
        try:
            info = await filing.maybe_autofile(sess)
        finally:
            filing.classify_session = orig                  # type: ignore[assignment]
        return sess, info

    sess, info = asyncio.run(good_conf())
    check("置信度够 → 自动归好", bool(info and info.get("applied")) and sess.subject == "物理",
          f"{sess.subject} / {sess.textbook}")

    if args.live:
        print("\n===== 6) 真模型判断一次（联网）=====")
        sess = make_session("filing_live", turns=3)
        info = asyncio.run(filing.classify_session(sess))
        print("     模型判断:", info)
        check("判断出来了", bool(info), str(info))
        if info:
            check("学科是数学", "数学" in info["subject"], info["subject"])
            check("教材能对到 9709（或老实说不确定）",
                  "9709" in info["textbook"] or info["textbook"] == "未指定教材",
                  info["textbook"])
            check("有置信度和理由", info["confidence"] > 0 and bool(info["reason"]), str(info.get("reason")))

    # 收尾：清掉测试会话与文件夹登记
    for sid in [s.id for s in [make_session("filing_apply"), make_session("filing_userrule")]]:
        pass
    for sid in ("filing_excerpt", "filing_needs1", "filing_needs2", "filing_needs3", "filing_needs4",
                "filing_needs5", "filing_apply", "filing_userrule", "filing_empty",
                "filing_lowconf", "filing_goodconf", "filing_live", "filing_check"):
        STORE.drop(sid)

    print()
    if _failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{_failures} 项失败 ✗")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
