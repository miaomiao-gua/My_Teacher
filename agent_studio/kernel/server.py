"""Agent 服务的 HTTP 薄壳（FastAPI）。

定位：**只做协议转换，不放业务逻辑**。
- 输入：HTTP 请求（JSON）
- 输出：SSE 事件流（内核产出什么就转什么）
- 文件：把会话工作目录、插件资源目录挂出来给前端访问

为什么另起一个服务而不是塞进老的 Flask（:5000）
------------------------------------------------
1. 老服务是 8384 行的单体，动它的风险远大于收益；新服务可以**并行跑、随时关**
2. Agent 需要异步工具执行、并发、取消、长连接——这是 async 框架的主场
3. 交接期两套并存：老界面继续可用，新界面按阶段接过来，出问题立刻回退

启动：`py -3.14 run_agent.py`（默认 http://127.0.0.1:5001）
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, events
from .config import CLIENT_DIR, PLUGINS_DIR, agent_settings, llm_configs
from .llm import probe_chain
from .paths import describe as paths_describe
from .plugins import list_plugins, reload_plugins
from .registry import describe_tools
from .runtime import AgentRuntime, cancel
from .sandbox import list_files
from . import session as kernel_session          # 文件夹登记表相关的模块级函数
from .session import STORE, safe_relpath

_PROCESS_STARTED = time.time()          # 供 /agent/version 判断「这份进程有多老」

app = FastAPI(title="My_Teacher Agent", version=__version__)

# 本地开发：老前端在 :5000，新前端可能在 :5001 或直接 file://，一律放行
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

RUNTIME = AgentRuntime()


@app.on_event("startup")
async def _start_background_tasks() -> None:
    """起一个后台循环盯着「提醒」：到点就发到桌宠总线上。

    放在服务端而不是桌宠里，是因为定提醒的可能是模型（学生不在电脑前也会到点），
    而且桌宠关了再开，提醒不会丢。
    """
    from .pet_bus import reminder_loop

    if not getattr(app.state, "_pet_reminder_task", None):
        app.state._pet_reminder_task = asyncio.create_task(reminder_loop())


@app.middleware("http")
async def no_cache_for_ui(request: Request, call_next):
    """前端的 JS / 插件界面一律不缓存。

    为什么必须这样：改了渲染器以后，浏览器会拿缓存里的旧 JS 继续跑，
    用户看到的现象是「改了但没变化」，然后以为是没修好——这类「假故障」最耗时间。
    开发期宁可每次都重新下载（文件都很小）。
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path.startswith("/agent/plugins/") or path in ("/agent/app", "/agent/pet"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


# ---------------------------------------------------------------------------
# 鉴权：过渡期默认「不拦」，但带 token 时会解析出用户名（为后续课程归属做准备）
# ---------------------------------------------------------------------------
def _username_of(request: Request) -> Optional[str]:
    token = ""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    token = token or (request.headers.get("X-Auth-Token") or "").strip()
    if not token:
        return None
    try:
        import sys

        from .config import LEGACY_SERVER_DIR

        # 老项目装了用户/会话表（SQLite），本项目可选地复用它的登录态；
        # 拿不到就当作「未登录」，不影响本项目单独运行。
        if str(LEGACY_SERVER_DIR) not in sys.path:
            sys.path.insert(0, str(LEGACY_SERVER_DIR))
        import auth  # type: ignore

        return auth.get_user_by_token(token)
    except Exception:
        return None


def _require_auth_enabled() -> bool:
    return bool(agent_settings().get("require_auth"))


# ---------------------------------------------------------------------------
# 健康检查与自省
# ---------------------------------------------------------------------------
@app.get("/agent/health")
async def health() -> Dict[str, Any]:
    configs = llm_configs()
    chain = await probe_chain(configs)
    return {
        "ok": any(c["ok"] for c in chain),
        "version": __version__,
        "chain": chain,
        "tools": len(describe_tools()),
        "plugins": [p["name"] for p in list_plugins() if p.get("loaded")],
        "settings": agent_settings(),
    }


@app.get("/agent/version")
async def version() -> Dict[str, Any]:
    """报告「当前服务到底跑的是哪份代码」。

    为什么需要它：进程可能是几小时前启动的，而你刚改完代码 ——
    这时看到的现象全是旧代码的行为，很容易误判成「改了没用」。
    （我在这个坑里绕过两圈：明明修好了校验器，用户测到的还是旧行为。）
    返回关键文件的修改时间，对比一下就知道该不该重启。
    """
    watched = {
        "server": Path(__file__),
        "runtime": Path(__file__).with_name("runtime.py"),
        "mathviz": Path(__file__).parent / "tools" / "mathviz.py",
        "web": Path(__file__).parent / "tools" / "web.py",
        "renderer": CLIENT_DIR / "static" / "js" / "agent_ui.js",
    }
    files = {}
    for name, path in watched.items():
        try:
            files[name] = {
                "mtime": int(path.stat().st_mtime),
                "mtime_text": datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M:%S"),
            }
        except OSError:
            files[name] = None
    return {
        "version": __version__,
        "started_at": datetime.fromtimestamp(_PROCESS_STARTED).strftime("%m-%d %H:%M:%S"),
        "now": datetime.now().strftime("%m-%d %H:%M:%S"),
        "tools": len(describe_tools()),
        "files": files,
    }


@app.get("/agent/tools")
async def tools_index() -> Dict[str, Any]:
    return {"tools": describe_tools()}


@app.get("/agent/plugins")
async def plugins_index() -> Dict[str, Any]:
    return {"plugins": list_plugins(), "dir": str(PLUGINS_DIR)}


@app.get("/agent/settings")
async def get_settings() -> Dict[str, Any]:
    """当前 Agent 设置（含「主力用云端还是本地」）。"""
    from .config import load_agent_settings

    return {"settings": load_agent_settings(), "chain": [c.label for c in llm_configs()]}


@app.post("/agent/settings")
async def post_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """切换主力模型：{"llm_provider": "cloud" | "local" | "auto", "fallback_local": true}。

    设置落盘在 `data/agent/settings.json`，改完立刻生效（下一条消息就走新链路），不用重启。
    """
    from .config import load_agent_settings, save_agent_settings

    allowed = {"llm_provider", "fallback_local"}
    patch = {k: v for k, v in (payload or {}).items() if k in allowed}
    save_agent_settings(patch)
    RUNTIME.configs = llm_configs()
    return {"ok": True, "settings": load_agent_settings(), "chain": [c.label for c in RUNTIME.configs]}


@app.post("/agent/plugins/reload")
async def plugins_reload() -> Dict[str, Any]:
    """热重载插件（调插件时不用重启服务——这是「多调试几轮」的前提）。"""
    return {"plugins": reload_plugins()}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@app.get("/agent/session/{session_id}")
async def session_info(session_id: str) -> Dict[str, Any]:
    """单个会话的完整信息：含历史消息与「最近一次界面」（切回对话时恢复工作台）。"""
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    # 只回给学生看的角色（工具调用过程不回放，避免切回来满屏工具日志）
    history = [
        {"role": m.get("role"), "content": m.get("content") or "", "ts": m.get("ts")}
        for m in sess.messages
        if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
    ]
    return {
        **sess.public(),
        "messages": history,
        "last_ui": (sess.meta or {}).get("last_ui"),
        "files": list_files(sess.workspace),
        # 归属是谁定的：学生自己拖的（user）还是 AI 归档的（model）。
        # 界面上用它显示「AI 自动归档」的小标记，排查时也能看出是谁覆盖了谁。
        "filed_by": (sess.meta or {}).get("filed_by"),
        "filed_reason": (sess.meta or {}).get("filed_reason"),
    }


@app.get("/agent/sessions")
async def sessions_tree() -> Dict[str, Any]:
    """左侧历史栏的数据：学科 → 教材 → 对话 三级树。"""
    return {"tree": STORE.tree(), "total": len(STORE.list_all())}


@app.post("/agent/session/new")
async def session_new(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    """在某个「学科 / 教材」下新建一个对话（左侧栏点「+」时调用）。"""
    subject = str(payload.get("subject") or "").strip()
    textbook = str(payload.get("textbook") or "").strip()
    lesson = str(payload.get("lesson_folder") or "").strip() or None
    sess = STORE.get_or_create(None, username=_username_of(request), lesson_folder=lesson)
    sess.subject = subject
    sess.textbook = textbook
    sess.title = str(payload.get("title") or "").strip()
    sess.save()
    return {"ok": True, "session": sess.public()}


@app.post("/agent/session/{session_id}/meta")
async def session_meta(session_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """改会话的归属或标题（重命名、移动到别的教材下）。

    注意：**用户手动改过归属要记一笔**（meta.filed_by = user），
    这样自动归档就不会再把他的选择覆盖掉（见 kernel/filing.py 的两条硬规则）。
    """
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    touched_folder = False
    for field_name in ("subject", "textbook", "title"):
        if field_name in payload:
            value = str(payload.get(field_name) or "").strip()
            if field_name in ("subject", "textbook") and value != getattr(sess, field_name, ""):
                touched_folder = True
            setattr(sess, field_name, value)
    if touched_folder:
        sess.meta = dict(getattr(sess, "meta", None) or {})
        sess.meta["filed_by"] = "user"
        sess.meta["filed_at"] = time.time()
        try:
            from .session import ensure_folder
            if (sess.subject or "").strip():
                ensure_folder(sess.subject, sess.textbook)
        except Exception:
            pass
    sess.save()
    return {"ok": True, "session": sess.public()}


@app.post("/agent/session/{session_id}/autofile")
async def session_autofile(session_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """让 AI 判断这段对话该归到哪个文件夹（网页版可以放个「帮我归档」按钮）。

    `force=true` 时忽略「刚试过」的冷却时间；`by=user` 时视为用户指定（会覆盖模型判断）。
    """
    from .filing import apply_filing, classify_session, needs_filing

    payload = payload or {}
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if payload.get("subject"):                    # 用户直接指定
        changed = apply_filing(sess, str(payload["subject"]), str(payload.get("textbook") or ""),
                               by="user", reason="用户指定")
        return {"ok": True, "changed": changed, "by": "user",
                "subject": sess.subject, "textbook": sess.textbook}
    if not needs_filing(sess) and not payload.get("force"):
        return {"ok": True, "changed": False, "skipped": "already_filed",
                "subject": sess.subject, "textbook": sess.textbook}
    info = await classify_session(sess)
    if not info:
        return {"ok": False, "changed": False, "error": "模型没能判断出归属（保持原样）",
                "subject": sess.subject, "textbook": sess.textbook}
    changed = apply_filing(sess, info["subject"], info.get("textbook", ""),
                           by="model", reason=info.get("reason", ""))
    return {"ok": True, "changed": changed, "by": "model", **info,
            "subject": sess.subject, "textbook": sess.textbook}


@app.delete("/agent/session/{session_id}")
async def session_delete(session_id: str) -> Dict[str, Any]:
    """删除一个对话（历史栏右键删除）。"""
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    STORE.drop(session_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 文件夹（学科 / 教材）：像资源管理器那样管理历史对话的分组
# ---------------------------------------------------------------------------
@app.get("/agent/folders")
async def folders_list() -> Dict[str, Any]:
    return {"folders": kernel_session.load_folders()}


@app.post("/agent/folders")
async def folders_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    """新建文件夹（空白处右键 → 新建学科 / 教材）。允许先建空的。"""
    subject = str(payload.get("subject") or "").strip()
    textbook = str(payload.get("textbook") or "").strip()
    if not subject and not textbook:
        raise HTTPException(status_code=400, detail="至少要给一个名字")
    items = kernel_session.ensure_folder(subject, textbook)
    return {"ok": True, "folders": items, "tree": STORE.tree()}


@app.post("/agent/folders/rename")
async def folders_rename(payload: Dict[str, Any]) -> Dict[str, Any]:
    """重命名 / 移动文件夹（里面的对话会跟着走）。"""
    result = kernel_session.rename_folder(
        str(payload.get("subject") or ""),
        str(payload.get("textbook") or ""),
        None if payload.get("new_subject") is None else str(payload.get("new_subject")),
        None if payload.get("new_textbook") is None else str(payload.get("new_textbook")),
    )
    return {"ok": True, **result, "tree": STORE.tree()}


@app.delete("/agent/folders")
async def folders_delete(subject: str = "", textbook: str = "", delete_sessions: bool = True) -> Dict[str, Any]:
    """删除文件夹。默认连同里面的对话一起删（与资源管理器一致）。"""
    result = kernel_session.delete_folder(subject, textbook, delete_sessions)
    return {"ok": True, **result, "tree": STORE.tree()}


@app.post("/agent/session/{session_id}/reset")
async def session_reset(session_id: str) -> Dict[str, Any]:
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    sess.messages.clear()
    sess.artifacts.clear()
    sess.save()
    return {"ok": True, "session": sess.public()}


@app.post("/agent/cancel")
async def cancel_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    sess = STORE.get(str(payload.get("session_id") or ""))
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    cancel(sess)
    return {"ok": True}


@app.post("/agent/quiz_result")
async def quiz_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """答题卡本地判分后自动回传，把对错记进学生画像。

    为什么要有这个接口：判分在前端**就地完成**（点一下立刻知道对错，零延迟、不烧模型），
    但「学习数据」必须落到画像里 —— 否则「记住这个学生、知道他哪里薄弱」这条线就断了。
    前端判完分调它一次，画像就更新了，完全不需要模型参与。
    """
    from .tools.lesson import apply_answer

    session_id = str(payload.get("session_id") or "")
    concept = str(payload.get("concept") or "").strip() or "未标注知识点"
    correct = bool(payload.get("correct"))
    note = str(payload.get("note") or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    sess = STORE.get(session_id)
    learner_id = (getattr(sess, "username", None) if sess else None) or "anonymous"
    folder = getattr(sess, "lesson_folder", None) if sess else None
    try:
        info = await apply_answer(learner_id, folder, concept, correct, note)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"记录失败：{exc}")
    # 交卷也让桌宠说一句（分数从 apply_answer 的返回里取得到就用它）
    try:
        from .pet_bus import BUS
        BUS.publish({"type": "quiz", "data": {"percent": info.get("percent") or info.get("score"),
                                              "concept": concept}})
    except Exception:
        pass
    return {"ok": True, **info}


# ---------------------------------------------------------------------------
# 对话（SSE）
# ---------------------------------------------------------------------------
def _prepare_session(payload: Dict[str, Any], request: Request):
    session_id = str(payload.get("session_id") or "").strip() or None
    lesson_folder = str(payload.get("lesson_folder") or "").strip() or None
    username = _username_of(request)
    if _require_auth_enabled() and not username:
        raise HTTPException(status_code=401, detail="未登录")
    sess = STORE.get_or_create(session_id, username=username, lesson_folder=lesson_folder)
    if lesson_folder:
        sess.lesson_folder = lesson_folder
    return sess


async def _stream(sess, message: str, extra: str) -> AsyncIterator[str]:
    # 同一个会话串行处理：学生连发两条消息时排队，而不是并发改同一份历史
    async with sess.lock:
        try:
            async for event in RUNTIME.run(sess, message, system_extra=extra):
                # 顺手抄一份给桌宠（见 pet_bus 的说明）：**只在这一处 mirror**，
                # 内核逻辑一行都不用改，桌宠就拿到了「老师在干什么」。
                try:
                    _pet_mirror(event)
                except Exception:
                    pass
                yield event.to_sse()
        except asyncio.CancelledError:  # 前端断开连接
            cancel(sess)
            raise
        except Exception as exc:  # noqa: BLE001
            yield events.error(f"{type(exc).__name__}: {exc}").to_sse()
            yield events.run_end("", "error", 0, 0).to_sse()


# ---------------------------------------------------------------------------
# 桌宠（事件流 + 提醒）
# ---------------------------------------------------------------------------

def _pet_mirror(event: Any) -> None:
    """把主流程的事件翻译成桌宠关心的几条，发到总线上。

    只挑桌宠用得上的（说话有意义的），不然它会一直冒气泡。
    文案由 `pet_bus.bubble_text_for` 统一生成（桌面版和网页版共用一套口径）。
    """
    from .pet_bus import publish_with_text

    name = getattr(event, "type", None) or (event.get("type") if isinstance(event, dict) else None)
    data = getattr(event, "data", None) or (event.get("data") if isinstance(event, dict) else {}) or {}
    if name == "run.start":
        publish_with_text({"type": "run.start", "data": {}})
    elif name == "run.end":
        publish_with_text({"type": "run.end", "data": {"reason": data.get("reason"),
                                                       "steps": data.get("steps")}})
    elif name == "tool.result":
        publish_with_text({"type": "tool.result", "data": {"name": data.get("name"),
                                                           "ok": data.get("ok")}})
    elif name == "error":
        publish_with_text({"type": "alert", "data": {"text": str(data.get("message") or "")[:120]}})


@app.get("/agent/pet/sprite")
async def pet_sprite(state: str = "idle", scale: float = 1.0) -> Any:
    """桌宠形象（PNG）。网页版桌宠用它做**兜底形象** —— 和桌面版同一只角色。

    为什么放服务端渲染：形象是 Pillow 现场画的（`desktop/pet_art.py`），
    服务端画一次就能给网页版用，不用在 JS 里再画一遍（两套画法迟早会不一致）。
    """
    from io import BytesIO

    from PIL import Image

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from desktop import pet_art

    st = state if state in pet_art.STATES else "idle"
    img = pet_art.render_state(st)
    if scale and scale != 1.0:
        s = max(0.4, min(4.0, float(scale)))
        img = img.resize((int(img.width * s), int(img.height * s)), Image.LANCZOS)
    # 兜底形象是贴在**页面**上的（不是色键透明窗口），所以不要压边，直接用 RGBA
    buf = BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=600"})


@app.get("/agent/pet/events")
async def pet_events(request: Request) -> StreamingResponse:
    """桌宠订阅的事件流（SSE）。服务端不需要知道桌宠是谁，挂了也不影响上课。"""
    from .pet_bus import BUS

    q = BUS.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            # 补报最近的提醒：桌宠晚一点启动，也不该错过刚定的事
            for ev in BUS.recent_reminders(within_seconds=1800):
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"          # 心跳，防止中间层掐连接
        finally:
            BUS.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/agent/reminder")
async def make_reminder(payload: Dict[str, Any]) -> Dict[str, Any]:
    """定提醒（桌宠右键菜单用；模型走 set_reminder 工具，写的是同一份数据）"""
    from .pet_bus import add_reminder, pending_reminders

    try:
        minutes = float(payload.get("minutes") or 20)
    except Exception:
        minutes = 20.0
    item = add_reminder(minutes, str(payload.get("text") or "该复习啦"), source="pet")
    return {"ok": True, "reminder": item, "pending": len(pending_reminders())}


@app.get("/agent/reminder")
async def list_reminders() -> Dict[str, Any]:
    """看还有哪些提醒没到点（设置面板里要列出来，否则学生定了提醒就再也看不见了）。

    只回未到点的，按时间排好，并顺手给一个「还有几分钟」——
    页面上不该自己算时间差（服务器和浏览器的钟未必一致）。
    """
    import time as _time

    from .pet_bus import pending_reminders

    now = _time.time()
    items = []
    for r in pending_reminders():
        try:
            due = float(r.get("due") or 0)
        except Exception:
            due = 0.0
        items.append({
            "id": r.get("id"), "text": r.get("text") or "该复习啦",
            "due": due, "in_minutes": round(max(0.0, due - now) / 60.0, 1),
            "source": r.get("source") or "",
        })
    items.sort(key=lambda x: x["due"])
    return {"ok": True, "count": len(items), "reminders": items}


@app.get("/agent/pet/window")
async def get_pet_window() -> Dict[str, Any]:
    """桌面小窗的窗口偏好（置顶 / 收起顶栏 / 位置 / 尺寸）。

    为什么存在服务端而不是只放浏览器的 localStorage：**桌面版启动小窗时要先读到它**
    才知道窗口放哪儿、要不要置顶。localStorage 是浏览器配置目录里的东西，
    启动器和它是两个进程，读不到 —— 走服务端才是最省事又一定对的做法。
    """
    from desktop import core as dcore
    from runtime_paths import data_root

    return {"ok": True, "prefs": dcore.load_window_prefs(data_root())}


@app.post("/agent/pet/window")
async def set_pet_window(payload: Dict[str, Any]) -> Dict[str, Any]:
    """网页里改窗口偏好 → 存到 data/pet_window.json（桌面小窗下次启动照它来）"""
    from desktop import core as dcore
    from runtime_paths import data_root

    dcore.save_window_prefs(data_root(), payload or {})
    return {"ok": True, "prefs": dcore.load_window_prefs(data_root())}


@app.get("/agent/pet/status")
async def pet_status() -> Dict[str, Any]:
    """桌宠/桌面壳自检用：总线有几个订阅者、还有几条提醒"""
    from .pet_bus import BUS, pending_reminders

    return {"ok": True, "subscribers": BUS.subscriber_count,
            "pending_reminders": len(pending_reminders()),
            "paths": paths_describe()}


SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",   # 关掉反代的缓冲，否则流式会被攒成一坨
}


@app.post("/agent/chat")
async def chat(request: Request) -> StreamingResponse:
    payload: Dict[str, Any] = await request.json()
    message = str(payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message 不能为空")
    sess = _prepare_session(payload, request)
    extra = str(payload.get("system_extra") or "")
    return StreamingResponse(_stream(sess, message, extra), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/agent/chat")
async def chat_get(request: Request, message: str, session_id: str = "", lesson_folder: str = "") -> StreamingResponse:
    """GET 版：方便浏览器 EventSource 直接试，也方便用 curl 排查。"""
    if not message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")
    sess = _prepare_session(
        {"session_id": session_id, "lesson_folder": lesson_folder}, request
    )
    return StreamingResponse(_stream(sess, message, ""), media_type="text/event-stream", headers=SSE_HEADERS)


# ---------------------------------------------------------------------------
# 静态资源：会话产物 + 插件界面
# ---------------------------------------------------------------------------
@app.get("/agent/files/{session_id}/{path:path}")
async def serve_session_file(session_id: str, path: str):
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    rel = safe_relpath(path)
    target = (sess.workspace / rel).resolve()
    workspace = sess.workspace.resolve()
    # 双保险：解析后的真实路径必须还在工作目录内
    if workspace not in target.parents and target != workspace:
        raise HTTPException(status_code=403, detail="越界访问")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@app.get("/agent/plugins/{name}/{path:path}")
async def serve_plugin_asset(name: str, path: str):
    plugin_dir = (PLUGINS_DIR / safe_relpath(name)).resolve()
    if PLUGINS_DIR.resolve() not in plugin_dir.parents:
        raise HTTPException(status_code=403, detail="越界访问")
    target = (plugin_dir / safe_relpath(path)).resolve()
    if plugin_dir not in target.parents and target != plugin_dir:
        raise HTTPException(status_code=403, detail="越界访问")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="插件资源不存在")
    return FileResponse(target, headers={"Cache-Control": "no-store"})


@app.get("/", include_in_schema=False)
@app.get("/agent/app")
async def agent_app() -> FileResponse:
    """工作台页面本体（本项目自带的前端，不依赖老项目的 client/）。"""
    page = CLIENT_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="web/index.html 不存在")
    return FileResponse(page, headers={"Cache-Control": "no-store, must-revalidate"})


@app.get("/agent/pet")
async def pet_page() -> FileResponse:
    """网页版桌宠（Live2D 优先，拿不到运行时就退回服务端画的形象）。"""
    page = CLIENT_DIR / "pet.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="web/pet.html 不存在")
    return FileResponse(page, headers={"Cache-Control": "no-store, must-revalidate"})


def _live2d_roots():
    """Live2D 模型可以放的地方，按优先级：

    1. `web/static/models/my_teacher/` —— 放进项目里就跟着 exe 一起走（**完全离线**，推荐）
    2. 老项目的 `client/static/models/my_teacher/` —— 用户本来就有这套模型，
       先直接拿来用（14 MB 不必复制一份）；一旦项目里放了一份，自动优先用项目里那份。
    3. 两处的 `models/` 根 —— 兜底：直接把模型文件散放在 models/ 下也能用。
    """
    from .config import LEGACY_ROOT

    out = [
        CLIENT_DIR / "static" / "models" / "my_teacher",
        LEGACY_ROOT / "client" / "static" / "models" / "my_teacher",
        CLIENT_DIR / "static" / "models",
        LEGACY_ROOT / "client" / "static" / "models",
    ]
    return [p for p in out if p.is_dir()]


# Live2D 渲染运行时（三个 JS）。**必须放本机**，否则页面的「本地优先」这一档永远是空的。
LIVE2D_VENDOR_FILES = ("pixi.min.js", "live2dcubismcore.min.js", "cubism4.min.js")


def _vendor_dirs():
    """运行时文件的存放位置：先看**用户数据目录**（exe 也写得进、升级不丢），再看项目里。"""
    from .config import DATA_DIR

    return [DATA_DIR / "live2d_vendor", CLIENT_DIR / "static" / "vendor" / "live2d"]


@app.get("/agent/live2d/vendor/{name}")
async def serve_live2d_vendor(name: str) -> FileResponse:
    if name not in LIVE2D_VENDOR_FILES:
        raise HTTPException(status_code=404, detail="只提供这三个运行时文件")
    for d in _vendor_dirs():
        f = d / name
        if f.is_file():
            return FileResponse(f, media_type="application/javascript",
                                headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(status_code=404, detail=f"运行时还没存到本机：{name}")


@app.post("/agent/live2d/vendor")
async def save_live2d_vendor(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把浏览器下载到的 Live2D 运行时存到本机（页面上的「存到本机」按钮用）。

    为什么绕这一圈：**这台机器本身连不上那几个 CDN**（实测 jsdelivr / unpkg / npmmirror
    全部被拒），但用户自己的**浏览器**可能有网。于是让浏览器下一遍、交给服务端落盘，
    之后就完全离线可用——不用用户手动去找文件，也不会「一打包成 exe 就失效」。
    """
    files = payload.get("files") or {}
    if not isinstance(files, dict):
        raise HTTPException(status_code=400, detail="files 必须是 {文件名: 内容}")
    out = _vendor_dirs()[0]
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for name, text in files.items():
        if name not in LIVE2D_VENDOR_FILES:
            continue
        body = text if isinstance(text, str) else ""
        # 大小检查是**必须的**：存进去的是要被浏览器执行的 JS，
        # 半截文件（比如返回了一个错误页）会让页面直接白屏，比没有更糟。
        if len(body) < 1000 or len(body) > 4_000_000:
            raise HTTPException(status_code=400, detail=f"{name} 大小不对（{len(body)} 字节），没有保存")
        if "html" in body[:200].lower():
            raise HTTPException(status_code=400, detail=f"{name} 看起来是网页而不是脚本，没有保存")
        (out / name).write_text(body, encoding="utf-8")
        saved.append(name)
    return {"ok": bool(saved), "saved": saved, "dir": str(out)}


@app.post("/agent/pet/model")
async def set_pet_model(payload: Dict[str, Any]) -> Dict[str, Any]:
    """换形象：记住学生选的这个模型（下次开界面还是它）。"""
    from . import pet_models

    mid = str(payload.get("id") or "").strip()
    item = pet_models.set_current_model(mid)
    if not item:
        raise HTTPException(status_code=404, detail=f"没有这个形象：{mid}")
    return {"ok": True, "current": {"id": item["id"], "name": item["name"], "url": item["url"]},
            "has_motions": item["has_motions"], "motion_groups": item["motion_groups"]}


@app.get("/agent/live2d/models")
async def live2d_models() -> Dict[str, Any]:
    """桌宠可以换哪些形象（扫盘得到，含每个模型自己的动作/表情名）。"""
    from . import pet_models

    data = pet_models.catalogue()
    data["ok"] = True
    return data


@app.get("/agent/live2d/model/{model_id}/{path:path}")
async def serve_live2d_model(model_id: str, path: str) -> FileResponse:
    """按形象 id 发模型文件（moc3 / 贴图 / 动作 / 表情）。

    必须**先解析出这个模型的目录**再看文件，且确认没跑出该目录 ——
    模型来自用户自己的各个文件夹，路径穿越在这里是真风险。
    """
    from . import pet_models

    target = pet_models.model_file(model_id, path)
    if target is None:
        raise HTTPException(status_code=404, detail=f"模型文件不存在：{model_id}/{path}")
    return FileResponse(target, headers={"Cache-Control": "public, max-age=3600"})


# 注意：这条 `{path:path}` 必须放在 /agent/live2d/vendor/... **后面**注册。
# FastAPI 按注册顺序匹配，通配路由在前会把 vendor 请求也吃掉（踩过一次：页面永远拿不到本地运行时）。
@app.get("/agent/live2d/{path:path}")
async def serve_live2d(path: str) -> FileResponse:
    """把 Live2D 模型文件（moc3 / 贴图 / 动作 / 表情）喂给网页。

    为什么不直接挂 StaticFiles：模型在两处（项目内、老项目里），
    而且要防止 `../` 穿越——这里逐层 resolve 后确认没跑出根目录才发。
    """
    rel = safe_relpath(path)
    for root in _live2d_roots():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            continue
        if target.is_file():
            return FileResponse(target, headers={"Cache-Control": "public, max-age=3600"})
    raise HTTPException(status_code=404, detail=f"Live2D 文件不存在：{rel}")


@app.get("/agent")
async def agent_index() -> JSONResponse:
    """给自己一个「服务活着」的入口（真正的界面在 /agent/app）。"""
    return JSONResponse(
        {
            "service": "My_Teacher Agent",
            "version": __version__,
            "hint": "先用 GET /agent/health 看模型链路是否就绪，再看 GET /agent/tools 有哪些工具。",
            "endpoints": [
                "GET  /agent/app      ← 试验台界面（浏览器直接打开）",
                "GET  /agent/health",
                "GET  /agent/tools",
                "GET  /agent/plugins",
                "POST /agent/plugins/reload",
                "POST /agent/chat   (SSE)",
                "GET  /agent/chat?message=...  (SSE)",
                "POST /agent/cancel",
                "GET  /agent/session/{id}",
                "GET  /agent/files/{id}/{path}",
                "GET  /agent/plugins/{name}/{path}",
            ],
        }
    )


# 静态资源（agent_ui.js 等）。必须放在路由定义之后：挂载会吃掉整个前缀。
if (CLIENT_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(CLIENT_DIR / "static")), name="client-static")
