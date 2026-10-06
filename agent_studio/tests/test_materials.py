#!/usr/bin/env python
"""找教材工具的自测：真搜索 / 链接探活 / 真下载 / 安全护栏。

跑法（agent_studio 目录下）：
    py -3.14 tests/test_materials.py                 # 全跑（需要联网）
    py -3.14 tests/test_materials.py --offline       # 只测安全护栏（不联网）

为什么值得单独测：这几个工具直接碰外网和文件系统 ——
"搜索没结果""下载了 0 字节""把内网地址下回来了" 三类问题都必须在这里挡住，
而不能等学生用了才发现。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.registry import ToolContext, get                    # noqa: E402
from kernel.session import STORE                                # noqa: E402
from kernel.tools import load_builtin_tools                     # noqa: E402
from kernel.tools.materials import probe_link, web_search       # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--query", default="人教版 高中数学 必修一 PDF 教材")
    args = parser.parse_args()

    load_builtin_tools()
    session = STORE.get_or_create("materials_test", username="mat_test")
    ctx = ToolContext(session=session, call_id="t", username="mat_test")

    print("===== 1) 安全护栏（内网 / 非法协议必须拦下）=====")
    for url in ["http://192.168.1.1/a.pdf", "http://127.0.0.1:5001/a.pdf",
                "http://localhost/a.pdf", "file:///C:/a.pdf", "ftp://x/a.pdf", ""]:
        info = probe_link(url)
        check(f"拦下 {url or '(空)'}", not info.get("ok"), info.get("note", ""))

    if args.offline:
        print("\n（--offline：跳过联网部分）")
        print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
        return 0 if _failures == 0 else 1

    print("\n===== 2) 真搜索 =====")
    try:
        results = web_search(args.query, limit=6)
        check("搜到结果", len(results) > 0, f"{len(results)} 条")
        for r in results[:3]:
            print(f"      · {r['title'][:56]}")
            print(f"        {r['url'][:88]}")
    except Exception as exc:  # noqa: BLE001
        check("搜索可用（联网）", False, f"{type(exc).__name__}: {exc}")
        results = []

    print("\n===== 3) 走工具的完整链路（search_materials 会探活 + 出候选表）=====")
    res = await get("search_materials").call(ctx, {"query": args.query, "kind": "教材", "limit": 5})
    check("search_materials 调通", bool(res.ok), (res.summary or "")[:90].replace("\n", " "))
    if res.ok and res.ui:
        items = res.ui.get("items") or []
        table = next((i for i in items if i.get("type") == "table"), None)
        check("给了候选表", bool(table), f"{len((table or {}).get('rows') or [])} 行")
        check("界面里明确写了「还没下载」", "还没下载" in str(res.ui.get("title") or ""))
        check("摘要里要求学生确认", "确认" in (res.summary or ""))

    print("\n===== 4) 真下载（用一个稳定可达的小文本文件验证通路）=====")
    # 注意：这里必须挑**本机网络确实能到达**的地址。实测境外站点（gnu.org 等）连不上，
    # 所以用国内可达的站点；这也正是搜索后端默认选 Bing 中国而不是 DuckDuckGo 的原因。
    dl = await get("download_material").call(ctx, {"url": "https://www.baidu.com/robots.txt"})
    check("download_material 调通", bool(dl.ok), (dl.summary or "")[:110].replace("\n", " "))
    if dl.ok:
        saved = (dl.data or {}).get("saved") or ""
        p = Path(saved)
        check("文件真的落盘且非空", p.exists() and p.stat().st_size > 20,
              f"{p.name} {p.stat().st_size if p.exists() else 0} 字节")

    print("\n===== 5) 非文档链接要被挡住（不能把网页当教材下）=====")
    bad = await get("download_material").call(ctx, {"url": "https://example.com/"})
    check("网页链接被拒绝下载", not bad.ok, (bad.summary or "")[:90])

    print("\n===== 6) list_materials =====")
    lm = await get("list_materials").call(ctx, {})
    print("     ", (lm.summary or "").split("\n")[0][:100])

    # 清理测试下载
    for f in session.workspace.glob("material*"):
        try:
            f.unlink()
        except OSError:
            pass

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
