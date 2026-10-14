#!/usr/bin/env python
"""「先站在别人整理好的大纲上，再备课」的自测。

学生要的行为（2026-10-14 提的）：
  在一个学科/教材目录里（例如 数学 / 9709）说「讲一下吧」→ **别反问「你想学哪个学科」**，而是：
  认代号/科目 → 去找官方考纲（考试代号）或公认课程大纲（Python 这类）→ 照它备课
  → 汇报「依据是哪一份 + 计划」→ 直接开讲第 1 课。

三件事必须成立：
  1. 提示词里真的有这条规则；有目录线索时还必须有「不要反问学科」那一句（没线索时不该有）
  2. prepare_course 会把查到的正文（`syllabus`）接到备课的 `document_markdown` 上 ——
     那套「按给定文档切分单元」的机制本来就在（`lesson_prep.prepare_lesson`），
     缺的只是没人把考纲抓回来、没人接上这根线
  3. **不传 `syllabus` 时行为完全不变**（向后兼容：老调用方、老测试不受影响）

跑法（agent_studio 目录下）：py -3.14 tests/test_syllabus_grounding.py
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

# ★ 本机 Python 的 stdout 默认是 GBK，打不出 ✗（U+2717）——
#   结果是「测试一失败就崩在编码上，看不到哪一项失败」（现有几个测试脚本都有这个雷）。
#   这里把输出改成 UTF-8：既不会崩，DSH 抓到的中文也不再是乱码。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.prompts import build_system_prompt                    # noqa: E402
from kernel.registry import ToolContext, describe_tools, get       # noqa: E402
from kernel.session import STORE                                   # noqa: E402
from kernel.tools import load_builtin_tools                        # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def _fake_plan() -> dict:
    return {
        "topic": "CIE A Level Mathematics 9709",
        "course_target": "按官方考纲把 P1 讲完",
        "acceptance_criteria": "能独立做 past paper 前 6 题",
        "units": [
            {"index": 1, "title": "Quadratics", "summary": "二次函数与判别式",
             "key_points": ["discriminant", "completing the square"]},
            {"index": 2, "title": "Functions", "summary": "定义域值域与复合",
             "key_points": ["domain", "range", "inverse"]},
        ],
    }


async def main() -> int:
    load_builtin_tools()

    print("===== 1) 提示词：有目录线索时要「别反问、先查大纲」 =====")
    sess = STORE.get_or_create("syllabus_test", username="syl_test")
    sess.subject, sess.textbook, sess.title = "数学", "9709", "你给我讲一下吧"
    prompt = build_system_prompt(sess, describe_tools())
    check("有目录线索 → 明确「不要反问学科」", "不要反问" in prompt)
    check("说了先去查考纲/公认大纲", "考纲" in prompt and "大纲" in prompt)
    check("说了走 prepare_course 的 syllabus 入参", "syllabus" in prompt)
    check("说了具体知识点提问照旧直接讲", "具体知识点" in prompt)
    check("提示词指向新工具 research_syllabus", "research_syllabus" in prompt)

    print("\n===== 2) 提示词：没目录线索时不该硬塞「别反问」 =====")
    plain = STORE.get_or_create("syllabus_plain", username="syl_test")
    plain.subject, plain.textbook, plain.title = "", "", ""
    p2 = build_system_prompt(plain, describe_tools())
    check("没线索 → 不出现「不要反问」", "不要反问" not in p2)
    check("查大纲的规则仍在（学生明说要做课时要能用）", "考纲" in p2 or "大纲" in p2)

    print("\n===== 3) 工具 schema：syllabus / syllabus_source 要暴露给模型 =====")
    tool = get("prepare_course")
    props = ((tool.parameters or {}).get("properties") or {}) if tool else {}
    check("schema 里有 syllabus", "syllabus" in props, str(list(props))[:120])
    check("schema 里有 syllabus_source", "syllabus_source" in props)

    print("\n===== 4) 接线：syllabus 要真的传到备课的 document_markdown =====")
    from kernel.tools import course as course_mod

    seen: dict = {}

    def fake_prepare_lesson(topic, config=None, document_markdown="", doc_toc=None, diagnosis=None):
        seen["topic"] = topic
        seen["document_markdown"] = document_markdown
        return _fake_plan()

    def fake_create_course(plan, username=None):
        return {"folder": "20261014_999999_test_9709", "units": plan["units"],
                "topic": plan["topic"], "unit_count": len(plan["units"]), "graph_ok": True}

    real_storage, real_prepare = course_mod.storage, course_mod.prepare_lesson
    course_mod.prepare_lesson = fake_prepare_lesson
    course_mod.storage = types.SimpleNamespace(create_course=fake_create_course)
    try:
        ctx = ToolContext(session=sess, call_id="t1", username="syl_test")
        syllabus_text = "# 9709 syllabus\n\n## Paper 1 Pure Mathematics 1\n1. Quadratics\n2. Functions"
        # 模拟「本会话真的读过这个来源」——用真实那条 URL（fetch_page 成功时会往这儿记一笔）
        real_url = ("https://www.cambridgeinternational.org/programmes-and-qualifications/"
                    "cambridge-international-as-and-a-level-mathematics-9709/")
        sess.meta["read_urls"] = [real_url]
        res = await get("prepare_course").call(ctx, {
            "topic": "CIE A Level Mathematics 9709",
            "syllabus": syllabus_text,
            "syllabus_source": real_url,
        })
        check("备课成功", bool(res.ok), (res.summary or "")[:80])
        check("正文原样传给了 document_markdown", seen.get("document_markdown") == syllabus_text,
              repr(seen.get("document_markdown"))[:100])
        check("依据来源出现在摘要里", "cambridgeinternational" in (res.summary or ""))
        check("读过 → 摘要标「真的读过」", "真的读过" in (res.summary or ""))
        ui_text = str(res.ui or "")
        check("依据来源出现在界面上", "cambridgeinternational" in ui_text)
        check("读过 → 界面标「已查证」", "已查证" in ui_text)
        check("课程绑定到会话", ctx.session.lesson_folder == "20261014_999999_test_9709")

        print("\n===== 4.5) 凭记忆写的来源必须被标成「未经查证」 =====")
        # 这就是 2026-10-14 实测那次：一次 search/fetch 都没调，却填了官网链接
        seen.clear()
        sess.meta["read_urls"] = []
        sess.lesson_folder = None
        ctx3 = ToolContext(session=sess, call_id="t3", username="syl_test")
        res3 = await get("prepare_course").call(ctx3, {
            "topic": "CIE A Level Mathematics 9709（凭记忆版）",
            "syllabus": "# 我凭记忆写的 9709 大纲",
            "syllabus_source": real_url,
        })
        check("没读过也照常备课（不拦着）", bool(res3.ok))
        check("摘要里明说「未经查证」", "未经查证" in (res3.summary or ""))
        check("摘要里点明可能是凭记忆写的", "凭记忆" in (res3.summary or ""))
        check("界面里也标了「未经查证」", "未经查证" in str(res3.ui or ""))

        print("\n===== 5) 向后兼容：不传 syllabus 时一行为都不变 =====")
        seen.clear()
        ctx2 = ToolContext(session=sess, call_id="t2", username="syl_test")
        res2 = await get("prepare_course").call(ctx2, {"topic": "Python 列表与字典"})
        check("没传 syllabus 时备课照旧成功", bool(res2.ok))
        check("document_markdown 是空串（老行为）", seen.get("document_markdown") == "")
        check("没依据时不编依据", "依据" not in (res2.summary or ""))
        # 实测：备完课它反问「你想从哪一课开始」，学生要的是马上开讲
        check("收尾要它直接开讲第 1 课", "开讲第 1 课" in (res2.summary or ""))
        check("并明说别再问从哪课开始", "不要再问" in (res2.summary or ""))

        print("\n===== 6) research_syllabus：真的读 + 缓存 + 备课自动取用 =====")
        from kernel.tools import materials as mat

        real_search, real_page = mat.web_search, mat._page_text
        long_text = "# 9709 syllabus\n" + "Quadratics Functions Coordinate geometry Circular measure " * 40

        def fake_search(q, limit=8):
            # 真实故障的形状：官方首页（权威域名、能读、45k 字导航）排在前面，
            # 真正有用的考纲页在后面 —— 必须选考纲页
            return [
                {"title": "Home", "url": "https://www.cambridgeinternational.org/", "snippet": ""},
                {"title": "9709 syllabus",
                 "url": ("https://www.cambridgeinternational.org/programmes-and-qualifications/"
                         "cambridge-international-as-and-a-level-mathematics-9709/"), "snippet": ""},
            ]

        mat.web_search = fake_search
        mat._page_text = lambda url: long_text
        try:
            sess.meta.pop("read_urls", None)
            sess.meta.pop("syllabus_cache", None)
            sess.lesson_folder = None
            ctx4 = ToolContext(session=sess, call_id="t4", username="syl_test")
            r4 = await get("research_syllabus").call(ctx4, {"topic": "9709"})
            check("工具读到了正文", bool(r4.ok), (r4.summary or "")[:80])
            check("首页与考纲页都能读时，选考纲页而不是首页",
                  "/programmes-and-qualifications/" in str((r4.data or {}).get("url") or ""),
                  str((r4.data or {}).get("url")))
            check("记进 read_urls（备课才能标已查证）",
                  "cambridgeinternational" in " ".join(sess.meta.get("read_urls") or []))
            check("正文缓存进会话", bool((sess.meta.get("syllabus_cache") or {}).get("text")))
            check("摘要告诉它接着调 prepare_course", "prepare_course" in (r4.summary or ""))

            seen.clear()
            ctx5 = ToolContext(session=sess, call_id="t5", username="syl_test")
            r5 = await get("prepare_course").call(ctx5, {"topic": "CIE A Level Mathematics 9709"})
            check("备课自动取用缓存正文（模型不用手抄）",
                  str(seen.get("document_markdown") or "").startswith("# 9709 syllabus"),
                  repr(seen.get("document_markdown"))[:60])
            check("并标成「真的读过」", "真的读过" in (r5.summary or ""))

            mat.web_search = lambda q, limit=8: []
            ctx6 = ToolContext(session=sess, call_id="t6", username="syl_test")
            r6 = await get("research_syllabus").call(ctx6, {"topic": "不存在的科目XYZ"})
            check("找不到来源时明确失败并要它如实说",
                  (not r6.ok) and ("没找到" in (r6.summary or "")), (r6.summary or "")[:70])

            print("\n===== 7) 官方考纲是 PDF：要下载转 Markdown，二进制垃圾要拒收 =====")
            real_dl, real_cv = mat.download_resource, mat.convert_document_to_markdown
            page_fake = mat._page_text
            markdown = "# 9709 syllabus\n" + ("Quadratics Coordinate geometry Circular measure " * 40)
            mat.download_resource = lambda res, d, prefix="x": Path(str(d)) / "s.pdf"
            mat.convert_document_to_markdown = lambda p: markdown
            mat.web_search = lambda q, limit=8: [
                {"title": "syllabus",
                 "url": "https://www.cambridgeinternational.org/Images/697427-2026-2027-syllabus.pdf",
                 "snippet": ""}]
            try:
                sess.meta.pop("read_urls", None)
                sess.meta.pop("syllabus_cache", None)
                ctx7 = ToolContext(session=sess, call_id="t7", username="syl_test")
                r7 = await get("research_syllabus").call(ctx7, {"topic": "9709"})
                cached = str((sess.meta.get("syllabus_cache") or {}).get("text") or "")
                check("PDF 走「下载 + 转 Markdown」", bool(r7.ok) and "Quadratics" in cached,
                      (r7.summary or "")[:70])
                check("缓存里是 Markdown 而不是 %PDF 字节", "%PDF" not in cached)

                # 二进制垃圾（PDF 字节被当 HTML 处理过）必须被拒收，不能当依据
                mat._page_text = lambda url: "%PDF-1.7\n" + "xref 0000000016 00000 n " * 60
                mat.web_search = lambda q, limit=8: [
                    {"title": "junk", "url": "https://example.edu/junk", "snippet": ""}]
                sess.meta.pop("syllabus_cache", None)
                ctx8 = ToolContext(session=sess, call_id="t8", username="syl_test")
                r8 = await get("research_syllabus").call(ctx8, {"topic": "随便一个主题"})
                check("二进制垃圾被拒收（不许当依据）",
                      (not r8.ok) and not (sess.meta.get("syllabus_cache") or {}).get("text"))
            finally:
                mat.download_resource, mat.convert_document_to_markdown = real_dl, real_cv
                mat._page_text = page_fake
        finally:
            mat.web_search, mat._page_text = real_search, real_page
    finally:
        course_mod.storage, course_mod.prepare_lesson = real_storage, real_prepare
        for sid in ("syllabus_test", "syllabus_plain"):
            STORE.drop(sid)

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
