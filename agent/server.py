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
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, events
from .config import CLIENT_DIR, PLUGINS_DIR, agent_settings, llm_configs
from .llm import probe_chain
from .plugins import list_plugins, reload_plugins
from .registry import describe_tools
from .runtime import AgentRuntime, cancel
from .sandbox import list_files
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


@app.middleware("http")
async def no_cache_for_ui(request: Request, call_next):
    """前端的 JS / 插件界面一律不缓存。

    为什么必须这样：改了渲染器以后，浏览器会拿缓存里的旧 JS 继续跑，
    用户看到的现象是「改了但没变化」，然后以为是没修好——这类「假故障」最耗时间。
    开发期宁可每次都重新下载（文件都很小）。
    """
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/") or path.startswith("/agent/plugins/") or path == "/agent/app":
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

        from .config import SERVER_DIR

        if str(SERVER_DIR) not in sys.path:
            sys.path.insert(0, str(SERVER_DIR))
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
    sess = STORE.get(session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {
        **sess.public(),
        "files": list_files(sess.workspace),
    }


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
                yield event.to_sse()
        except asyncio.CancelledError:  # 前端断开连接
            cancel(sess)
            raise
        except Exception as exc:  # noqa: BLE001
            yield events.error(f"{type(exc).__name__}: {exc}").to_sse()
            yield events.run_end("", "error", 0, 0).to_sse()


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


@app.get("/agent/app")
async def agent_app() -> FileResponse:
    """试验台页面：独立于老 Galgame 界面，用来验证「AI 生成界面」这条链路。"""
    page = CLIENT_DIR / "agent.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="client/agent.html 不存在")
    return FileResponse(page)


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
