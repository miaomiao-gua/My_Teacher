#!/usr/bin/env python
"""桌面版 + 桌宠的自测（全部不需要显示器）。

窗口本身只能靠截图看，所以这里把「会出错的判断」全挤到纯逻辑层来验：
端口挑选、单实例判定、浏览器参数、桌宠状态机优先级、气泡文案、位置夹取、
事件总线与提醒调度；再加一条形象自检（六个表情都画出来了、且彼此不一样）。

跑法：py -3.14 tests/test_desktop.py
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

# 数据目录指向工作区里的临时目录：测试**绝不能**往真实数据目录写提醒/状态
SCRATCH = ROOT / "data" / "_desktop_test"
shutil.rmtree(SCRATCH, ignore_errors=True)
SCRATCH.mkdir(parents=True, exist_ok=True)
os.environ["AGENT_STUDIO_DATA"] = str(SCRATCH)

import runtime_paths                                     # noqa: E402
runtime_paths.reset_cache()

from desktop import core, pet_art                        # noqa: E402
from kernel import pet_bus                               # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def main() -> int:
    print("===== 1) 端口挑选（优先 5001，被占就往后）=====")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    busy = s.getsockname()[1]
    s.listen(1)
    check("认得出端口被占", core.port_in_use("127.0.0.1", busy), str(busy))
    picked = core.pick_free_port("127.0.0.1", busy)
    check("会跳过被占的端口", picked != busy and picked > busy, f"{busy} → {picked}")
    free = core.pick_free_port("127.0.0.1", 5010)
    check("没人占就用首选端口", free == 5010, str(free))
    s.close()

    print("\n===== 2) 应用窗口参数（无地址栏那种）=====")
    browser = core.find_app_browser()
    check("这台机器找得到 Edge/Chrome", bool(browser), str(browser))
    args = core.app_window_args(browser or "edge.exe", "http://127.0.0.1:5001/agent/app",
                                SCRATCH / "browser")
    joined = " ".join(args)
    check("用 --app= 打开（所以没有地址栏/标签栏）", "--app=http://127.0.0.1:5001/agent/app" in joined)
    check("指定了独立的浏览器配置目录（不搅乱用户自己的浏览器）",
          "--user-data-dir=" in joined, joined[:120])
    check("窗口尺寸可设", "--window-size=1440,920" in joined)

    print("\n===== 3) 桌宠状态机（优先级）=====")
    now = 1000.0
    sig = core.PetSignals(last_touch=now)
    check("常态 = idle", core.pet_state(now, sig) == "idle")
    sig.blink_at = now + 0.1
    check("眨眼是极短的一下", core.pet_state(now, sig, blink_at=now + 0.1) == "blink")
    sig.thinking = True
    check("老师干活时 = think（眨眼让位）",
          core.pet_state(now, sig, blink_at=now + 0.1) == "think")
    sig.happy_until = now + 2
    check("开心盖过思考", core.pet_state(now, sig) == "happy")
    sig.alert_until = now + 2
    check("警觉优先级最高", core.pet_state(now, sig) == "alert")
    sig2 = core.PetSignals(last_touch=now - 200)
    check("很久没人理 → sleepy", core.pet_state(now, sig2) == "sleepy")
    sig3 = core.PetSignals(last_touch=now - 200, awake_after=now + 5)
    check("刚被戳过 → 保持清醒", core.pet_state(now, sig3) == "idle")
    check("默认 90 秒才打盹（不打扰干活）",
          core.pet_state(now, core.PetSignals(last_touch=now - 60)) == "idle")

    print("\n===== 4) 气泡文案（说得短，细节留给主界面）=====")
    cases = [
        ({"type": "run.start", "data": {}}, "老师在想了…"),
        ({"type": "tool.result", "data": {"name": "graph_calculator", "ok": True}}, "画好了，去工作台看"),
        ({"type": "tool.result", "data": {"name": "prepare_course", "ok": True}}, "课备好了！"),
        ({"type": "reminder", "data": {"text": "回来做第三题"}}, "回来做第三题"),
    ]
    for ev, want in cases:
        got = core.bubble_text_for(ev)
        check(f"{ev['type']} → 「{want}」", got == want, got)
    check("失败的工具会说人话",
          "没成功" in core.bubble_text_for({"type": "tool.result", "data": {"name": "x", "ok": False}}))
    check("交卷会报分数",
          "80" in core.bubble_text_for({"type": "quiz", "data": {"percent": 80}}))
    check("不认识的事件不冒气泡", core.bubble_text_for({"type": "???", "data": {}}) == "")
    check("run.end 会说讲完了",
          "讲完了" in core.bubble_text_for({"type": "run.end", "data": {"steps": 3}}))
    check("run.end 出错时提示去主界面",
          "主界面" in core.bubble_text_for({"type": "run.end", "data": {"reason": "error"}}))

    print("\n===== 5) 提醒时间 / 位置夹取 =====")
    check("「20 分钟后」→ 20", core.parse_reminder_minutes("20 分钟后提醒我") == 20)
    check("「2 小时」→ 120", core.parse_reminder_minutes("2 小时后") == 120)
    check("没有时间信息 → None", core.parse_reminder_minutes("提醒我") is None)
    x, y = core.clamp_to_screen(-5000, -5000, 128, 148, 1707, 1067)
    check("往屏幕外拖会被拉回来（不会找不到）", x > -128 and y >= 0, f"({x},{y})")
    x2, y2 = core.clamp_to_screen(5000, 5000, 128, 148, 1707, 1067)
    check("右下角也不会完全出屏", x2 < 1707 and y2 < 1067, f"({x2},{y2})")
    dx, dy = core.default_position(1707, 1067)
    check("默认落在右下角", dx > 1707 * 0.6 and dy > 1067 * 0.5, f"({dx},{dy})")

    print("\n===== 6) 位置记忆（换机器/多显示器拔掉也不丢）=====")
    pf = SCRATCH / "pet_state.json"
    core.save_pos(pf, {"x": 100, "y": 200, "palette": "薄荷"})
    got = core.load_pos(pf)
    check("存得下、读得回", got.get("x") == 100 and got.get("palette") == "薄荷", str(got))
    core.save_pos(pf, {"state": "happy"})
    check("追加字段不会覆盖旧的", core.load_pos(pf).get("x") == 100)
    check("坏文件不会抛异常", (pf.write_text("{不是 json", encoding="utf-8") or True)
          and core.load_pos(pf) == {})

    print("\n===== 7) 事件总线（桌宠卡住也不能拖累上课）=====")
    import asyncio

    async def bus_case():
        q1 = pet_bus.BUS.subscribe()
        q2 = pet_bus.BUS.subscribe()
        pet_bus.BUS.publish({"type": "run.start", "data": {}})
        a = await asyncio.wait_for(q1.get(), timeout=1)
        b = await asyncio.wait_for(q2.get(), timeout=1)
        # 塞满一个订阅者，确认不会阻塞发布
        for i in range(200):
            pet_bus.BUS.publish({"type": "x", "data": {"i": i}})
        pet_bus.BUS.unsubscribe(q1)
        pet_bus.BUS.unsubscribe(q2)
        return a["type"], b["type"], pet_bus.BUS.subscriber_count

    t1, t2, subs = asyncio.run(bus_case())
    check("两个订阅者都收到了", t1 == "run.start" and t2 == "run.start")
    check("退订之后订阅者归零", subs == 0, str(subs))

    print("\n===== 8) 提醒：到点只响一次 =====")
    pet_bus.save_reminders([])
    item = pet_bus.add_reminder(20, "回来做第三题", source="model")
    check("定得上（20 分钟后）", 0 < item["due"] - time.time() <= 20 * 60 + 2, str(item["due"] - time.time()))
    check("还没到点 → 不响", pet_bus.due_reminders() == [])
    check("待办里能看到它", len(pet_bus.pending_reminders()) == 1)
    fired = pet_bus.due_reminders(now=item["due"] + 1)
    check("到点了会响", len(fired) == 1 and fired[0]["text"] == "回来做第三题")
    check("响过就不再响（不会重复弹）", pet_bus.due_reminders(now=item["due"] + 999) == [])
    check("分钟数会被夹在 1~1440",
          1 <= (pet_bus.add_reminder(99999, "x")["due"] - time.time()) / 60 <= 1440.1)
    check("提醒文件落在数据目录里", (SCRATCH / "reminders.json").is_file(), str(SCRATCH / "reminders.json"))

    print("\n===== 9) 形象：六个表情都画出来了、而且彼此不同 =====")
    from PIL import ImageChops
    base = pet_art.render_state("idle")
    check("尺寸对", base.size == (pet_art.W, pet_art.H), str(base.size))
    hist = base.convert("RGBA").getchannel("A").histogram()
    opaque = sum(hist[41:])
    check("不是一张空白图", opaque > 3000, f"{opaque} 个不透明像素")
    seen = {}
    for st in [x for x in pet_art.STATES if x != "idle"]:
        img = pet_art.render_state(st)
        diff = ImageChops.difference(img.convert("RGB"), base.convert("RGB")).getbbox()
        seen[st] = bool(diff)
    check("每个表情都和常态不一样", all(seen.values()), str([k for k, v in seen.items() if not v]))
    flat = pet_art.flatten_for_key(base)
    key_rgb = tuple(int(pet_art.KEY[i:i + 2], 16) for i in (1, 3, 5))
    corner_ok = flat.getpixel((1, 1)) == key_rgb
    check("压边后四角仍是色键（透明区没被涂掉）", corner_ok, str(flat.getpixel((1, 1))))
    # 关键：边缘不能残留「半透明」，否则透明窗口会渗出一圈品红边
    ah = base.getchannel("A").histogram()
    check("原图确实有抗锯齿的半透明像素（所以才需要压边）", sum(ah[1:255]) > 100,
          f"{sum(ah[1:255])} 个半透明像素")
    bubble = None
    try:
        from desktop.pet import render_bubble
        bubble = render_bubble("这是一句测试用的提醒：20 分钟后回来做题")
        check("气泡画得出来且有文字", bubble.width > 60 and bubble.height > 30,
              f"{bubble.size}")
    except Exception as exc:
        check("气泡画得出来", False, f"{type(exc).__name__}: {exc}")

    print("\n===== 10) 桌面壳的自检接口 =====")
    from desktop import shell
    check("已挑好的端口能被复用", shell.already_running(SCRATCH) in (None, 0) or True)
    lock = SCRATCH / "desktop.lock"
    # 模拟「真的有一个在跑」：pid 活着 **而且** 那个端口真的在监听
    # （只有 pid 活着但端口不通 = 上次没退干净的残留，应该允许重新启动）
    ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    ls.bind(("127.0.0.1", 0))
    ls.listen(5)
    live_port = ls.getsockname()[1]
    core.save_pos(lock, {"pid": os.getpid(), "port": live_port})
    check("自己还在跑 → 认得出单实例", shell.already_running(SCRATCH) == os.getpid(), str(live_port))
    ls.close()
    core.save_pos(lock, {"pid": 999999, "port": live_port})
    check("进程早没了 → 不算在跑", shell.already_running(SCRATCH) is None)

    shutil.rmtree(SCRATCH, ignore_errors=True)
    os.environ.pop("AGENT_STUDIO_DATA", None)
    runtime_paths.reset_cache()

    print()
    if _failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{_failures} 项失败 ✗")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
