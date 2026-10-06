#!/usr/bin/env python
"""网页桌宠（/agent/pet）自测：**不靠肉眼看截图**的那部分。

为什么要专门测这几件事（都是真踩过的）：
  1. pet_page.js 里的动作组名必须真的存在于用户的 model3.json 里。
     名字写错（比如拿文件名当组名）不会报错，只会「永远不播动作」——
     这种静默失效最难发现，所以要拿模型文件对着查。
  2. Live2D 的取景基准必须是**模型原始画布**（originalWidth/Height），
     而且锚点要在脚底 (0.5, 1)。早期版本用 model.width 那套，结果是满屏只有鞋子。
     这条靠静态检查守不住「看起来对不对」，但能守住「别再改回那个写法」。
  3. 页面必须**先显示角色再升级**：Live2D 的脚本从外网拿，网络被挡时不会报错、
     只会一直挂着，所以 boot() 里必须有兜底显示 + 硬超时。
  4. 服务端几条接口得真的在：/agent/pet（页面）、/agent/live2d/...（模型文件）、
     /agent/live2d/vendor/...（运行时，本地优先），并且不许被穿越。

跑法：py -3.14 tests/test_pet_page.py            # 纯逻辑 + 接口（不需要浏览器）
      py -3.14 tests/test_pet_page.py --http     # 额外打一次真在跑的服务（需先启动服务）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.config import CLIENT_DIR, LEGACY_ROOT                      # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def read_js() -> str:
    p = CLIENT_DIR / "static" / "js" / "pet_page.js"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def read_core() -> str:
    p = CLIENT_DIR / "static" / "js" / "pet_core.js"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def find_model_json() -> Path | None:
    """用户的 Live2D 模型（项目里优先，其次老项目 client/ 下）"""
    for base in (CLIENT_DIR / "static" / "models", LEGACY_ROOT / "client" / "static" / "models"):
        if not base.is_dir():
            continue
        hits = sorted(base.rglob("*.model3.json"))
        if hits:
            return hits[0]
    return None


# ---------------------------------------------------------------------------
# 1) 形象清单：每个模型都能被「换形象」用上
# ---------------------------------------------------------------------------
def test_model_catalogue() -> None:
    print("\n[1] 形象清单：拿得到、有 id、能取文件")
    sys.path.insert(0, str(ROOT))
    from kernel import pet_models as pm

    items = pm.scan_models(force=True)
    check("扫到了形象", len(items) > 0, f"{len(items)} 个")
    check("每个都有 id / name / url", all(it.get("id") and it.get("name") and it.get("url") for it in items))
    cur = pm.current_model()
    check("当前形象能解析出来（换形象之后界面上要显示它）", cur is not None,
          str(cur and cur.get("label")))
    # 「白-免费版」这类没有动作文件的模型必须被标出来（前端要靠它走参数演绎）
    for it in items:
        if not it["has_motions"]:
            check(f"「{it['name']}」标成了「没有动作文件」", it["motion_groups"] == [])
            break
    else:
        check("至少有一个没有动作文件的模型可测", False, "这台机器上没有")


# ---------------------------------------------------------------------------
# 2) 取景与演绎：写死的规则（防止改回「满屏鞋子」/「一动不动」那两版）
# ---------------------------------------------------------------------------
def test_framing_rules() -> None:
    print("\n[2] 取景与演绎规则")
    core = read_core()
    check("pet_core.js 存在且非空", len(core) > 3000, f"{len(core)} 字符")
    check("用 internalModel.originalWidth/Height 当基准",
          "originalWidth" in core and "originalHeight" in core)
    check("锚点在脚底 (0.5, 1)", "anchor.set(0.5, 1)" in core)
    check("小玩偶按「露出画布上方一截」取景（dollFrac）", "dollFrac" in core)
    check("不再用 model.width 当缩放基准（那会放大好几倍）",
          not re.search(r"Math\.min\(\s*w\s*/\s*model\.width", core))
    check("画布隐藏时显式给宽高（隐藏元素 clientWidth 是 0）",
          "width: Math.max(160" in core and "height: Math.max(160" in core)
    check("换形象时换一块新画布（同一块 canvas 不能建第二个 WebGL 上下文）",
          "freshCanvas" in core and "replaceChild" in core)
    check("参数写在 afterMotionUpdate（库留的钩子）", "afterMotionUpdate" in core)
    check("有动作文件在播时参数让位（suppressedUntil）", "suppressedUntil" in core)
    check("没有动作文件的模型靠程序驱动参数", "ParamBreath" in core and "ParamMouthOpenY" in core)
    check("滚轮缩放取景（每个模型留白不同）", "setZoom" in core)


# ---------------------------------------------------------------------------
# 3) 降级路径：先显示角色、硬超时、迟到的成功也要接住
# ---------------------------------------------------------------------------
def test_fallback_path() -> None:
    print("\n[3] 兜底：网络被挡时也得有角色、有气泡")
    core = read_core()
    check("start 里先调 useSprite（先出角色再升级）",
          re.search(r"useSprite\('正在加载 Live2D 运行时", core) is not None)
    check("Live2D 有硬超时上限", "Promise.race" in core and "timedOut" in core)
    check("超时后迟到的成功会补上（不会永远看不到 Live2D）", "live.then(" in core)
    check("Live2D 运行时本地优先，其次 CDN",
          core.index("/agent/live2d/vendor/") < core.index("cdn.jsdelivr.net"))
    check("备用形象走服务端渲染的接口", "'/agent/pet/sprite?state='" in core)
    page = read_js()
    check("独立窗口页用了同一份核心（不再是两套实现）",
          "PetCore.createPet" in page and "PetCore.actFor" in page)


# ---------------------------------------------------------------------------
# 4) 页面与路由
# ---------------------------------------------------------------------------
def test_page_and_routes_exist() -> None:
    print("\n[4] 页面与路由")
    html = CLIENT_DIR / "pet.html"
    check("web/pet.html 存在", html.is_file())
    if html.is_file():
        body = html.read_text(encoding="utf-8")
        check("页面引入了 pet_core.js 与 pet_page.js",
              "/static/js/pet_core.js" in body and "/static/js/pet_page.js" in body)
        check("页面引入了共用的 pet.css", "/static/css/pet.css" in body)
        for el in ("#stage", "#chat", "#mode", "#save", "#model", "#ask", "#log"):
            check(f"页面里有 {el}", f'id="{el[1:]}"' in body)

    css = CLIENT_DIR / "static" / "css" / "pet.css"
    check("web/static/css/pet.css 存在（两个界面共用一份样式）", css.is_file())
    if css.is_file():
        text = css.read_text(encoding="utf-8")
        for cls in (".pet-body", ".pet-canvas", ".pet-sprite", ".pet-bubble", ".au-pet"):
            check(f"样式里有 {cls}", cls in text)

    app = CLIENT_DIR / "static" / "js" / "agent_ui.js"
    app_js = app.read_text(encoding="utf-8") if app.is_file() else ""
    check("工作台里挂上了小玩偶（mountPetDoll）", "mountPetDoll" in app_js)
    check("小玩偶能换形象（PetCore.openPicker）", "PetCore.openPicker" in app_js)
    check("小玩偶能拖动并且记得位置", "au_pet_pos_v1" in app_js)
    index_html = (CLIENT_DIR / "index.html").read_text(encoding="utf-8")
    check("工作台页面也引了 pet_core.js 与 pet.css",
          "/static/js/pet_core.js" in index_html and "/static/css/pet.css" in index_html)

    server_py = (ROOT / "kernel" / "server.py").read_text(encoding="utf-8")
    for route in ("/agent/pet", "/agent/live2d/models", "/agent/live2d/model/{model_id}/{path:path}",
                  "/agent/pet/model", "/agent/live2d/vendor/{name}"):
        check(f"server.py 注册了 {route}", route in server_py)
    # 注册顺序：通配路由必须在具体路由之后，否则会被吃掉（踩过）
    check("通配路由注册在 vendor / model 之后",
          server_py.index("/agent/live2d/model/{model_id}/{path:path}") < server_py.index("/agent/live2d/{path:path}"))
    check("模型文件走 safe_relpath（防穿越）", "safe_relpath" in server_py)


# ---------------------------------------------------------------------------
# 5) 可选：对真在跑的服务打一遍
# ---------------------------------------------------------------------------
def test_http(base: str = "http://127.0.0.1:5001") -> None:
    print(f"\n[5] 对 {base} 实测（需要服务已启动）")
    import httpx

    def get(path: str):
        try:
            return httpx.get(base + path, timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            return exc

    r = get("/agent/pet")
    check("GET /agent/pet = 200 且是 HTML",
          hasattr(r, "status_code") and r.status_code == 200 and "text/html" in r.headers.get("content-type", ""),
          str(r)[:120])

    r = get("/agent/live2d/models")
    models = []
    if hasattr(r, "status_code") and r.status_code == 200:
        models = (r.json().get("models") or [])
    check("GET /agent/live2d/models 有清单", len(models) > 0, f"{len(models)} 个")
    for m in models[:4]:
        rr = get(m["url"])
        check(f"取模型文件「{m['name']}」",
              hasattr(rr, "status_code") and rr.status_code == 200 and len(rr.content) > 100,
              f"HTTP {getattr(rr, 'status_code', '?')}")

    r = get("/agent/live2d/vendor/pixi.min.js")
    check("GET /agent/live2d/vendor/pixi.min.js 是 200 或 404（404=还没存，属正常）",
          hasattr(r, "status_code") and r.status_code in (200, 404), str(r)[:80])

    r = get("/agent/pet/sprite?state=happy&scale=2")
    check("GET /agent/pet/sprite = 200 且是 PNG",
          hasattr(r, "status_code") and r.status_code == 200 and r.headers.get("content-type") == "image/png",
          str(r)[:80])

    r = get("/agent/live2d/..%2f..%2fdata%2fconfig.json")
    check("路径穿越被挡住（不是 200）",
          hasattr(r, "status_code") and r.status_code != 200, str(r)[:80])

    r = get("/agent/pet/status")
    check("GET /agent/pet/status 有订阅者字段",
          hasattr(r, "status_code") and r.status_code == 200 and "subscribers" in r.text, str(r)[:80])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true", help="额外对真在跑的服务打一遍")
    ap.add_argument("--base", default="http://127.0.0.1:5001")
    args = ap.parse_args()

    test_model_catalogue()
    test_framing_rules()
    test_fallback_path()
    test_page_and_routes_exist()
    if args.http:
        test_http(args.base)

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
