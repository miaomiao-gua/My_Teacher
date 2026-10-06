"""AgentRuntime —— 主循环：模型 ⇄ 工具 ⇄ 事件流。

一个回合长这样::

    学生提问
      └─ 第 1 步：模型回话（可能同时要求调用若干工具）
           ├─ 执行工具 → 结果回灌进**同一个回合** → 第 2 步
           └─ 或：没有工具调用 → 本轮结束
      直到模型不再要求工具，或达到步数上限

和老架构的本质区别
------------------
老架构：模型输出文本标记 → 前端执行 → 结果靠另一个 HTTP 请求回灌（**跨回合**）。
新架构：工具执行结果当场回到模型的同一个上下文里（**同回合**），
所以「算错了再改」「代码报错再修」都变成模型自己一步能完成的事，不用提示词硬堵。

工程上的四个硬约束
------------------
- **兜底链路**：主力模型连接失败 → 自动切下一个（本地崩了不至于整节课卡死）
- **步数上限**：防止小模型陷入「反复调同一个工具」的死循环
- **工具结果短回灌**：`summary` 进模型上下文，`data`/`ui` 只走事件流
- **可取消**：学生中途插话/关页面，`session.cancel` 一置位，循环立刻退出
"""

from __future__ import annotations

import asyncio
import re
import time
import traceback
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Tuple

from . import events
from .config import agent_settings, llm_configs, LLMConfig
from .llm import BaseLLM, LLMError, ToolCallRequest, build_llm
from .prompts import build_system_prompt
from .registry import REGISTRY, ToolContext, ToolResult, describe_tools, get, tools_payload

TOOL_CONCURRENCY = 4

# 「只说不做 / 假装动手」检测。
#
# 实测遇到过的两种形态（都不是提示词能根治的，小模型和云端模型都会犯）：
#   形态一：承诺了却不动手 —— 「我这就为你做一个仿真」，然后直接结束回合（1 步、0 次工具调用）
#   形态二：**假装动手** —— 输出占位文字冒充执行结果，比如连写 7 次「（生成动态图像）」，
#           学生屏幕上一片空白，而正文看起来像已经做完了。这个更隐蔽，也更气人。
#
# 所以内核兜底：本轮**一次工具都没用过**时，只要回复里出现「动手类动词」配上
# 「稍等/正在/我来」这类预告，或者出现「（生成…）」这种假装动作的括号占位，
# 就自动追加一条催促让它真的动手（只催一次，避免拖成死循环）。
_PROMISE_RE = re.compile(
    r"(让我(开始|来|为你|先|现在)|我(这就|马上|立刻|先|将|会|来)(为你|给你|帮你|来)?|"
    r"我(生成|画|做|制作|搭建|写|建|设计|准备|查找|搜集|整理)|接下来我(会|将|要)|"
    r"我要(为你|给你)|为你(制作|生成|打造|搭建|编写|写|做|准备|查找)|"
    r"开始(制作|生成|搭建|编写|设计|准备)|现在就(来|开始)|"
    r"请稍等|请稍候|稍等片刻|稍等|正在(生成|绘制|制作|加载|创建|准备|查找|整理|搜集))"
)
_BUILD_RE = re.compile(
    r"(制作|生成|打造|搭建|编写|创建|设计|渲染|绘制|画个|画一|画张|画出|画一下|"
    r"写(一|个|出)|做(一|个|出)|演示|运行|算一下|计算一下|打开|展示|准备|备课|查找|搜集|整理)"
)
# 假装动作的占位符：全角/半角括号里包着动作词，例如「（生成动态图像）」「【绘制中】」
_FAKE_ACTION_RE = re.compile(
    r"[（(【\[](?:[^）)】\]]{0,14})(?:生成|绘制|画|加载|运行|创建|制作|处理|渲染|展示)"
    r"(?:[^）)】\]]{0,14})[）)】\]]"
)

NUDGE_MESSAGE = (
    "你刚才只是在描述计划或写占位文字（例如「（生成动态图像）」「请稍等片刻」），并没有真的调用工具——"
    "学生现在屏幕上是空的，而对话已经结束了。请立刻调用工具把它做出来："
    "**如果这门课还没有教案，先用 `prepare_course` 备课**；画任何图像（函数/圆/不等式）用 "
    "`graph_calculator`（只写公式，学生还能在上面改）、分步作图用 `draw_board`、"
    "做界面用 `render_ui` 或 `write_file`。"
    "不要再输出计划、预告或任何括号占位文字。"
)


def looks_like_promise(text: str) -> bool:
    """判断这段回复是不是「说了但没动手 / 假装动手」。"""
    if not text:
        return False
    tail = text[-800:]
    if _FAKE_ACTION_RE.search(tail):
        return True                      # 括号占位冒充结果：明确的假装动手
    return bool(_PROMISE_RE.search(tail)) and bool(_BUILD_RE.search(tail))


class AgentRuntime:
    def __init__(
        self,
        configs: Optional[Sequence[LLMConfig]] = None,
        settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.configs: List[LLMConfig] = list(configs or llm_configs())
        self.settings: Dict[str, Any] = settings or agent_settings()
        self._ensure_tools()

    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_tools() -> None:
        """导入内置工具模块（导入即注册）并加载插件。幂等。"""
        from . import tools as _tools  # noqa: F401

        _tools.load_builtin_tools()
        from .plugins import load_plugins

        if not load_plugins():
            load_plugins()

    # ------------------------------------------------------------------
    async def run(
        self, session: Any, user_message: str, *, system_extra: str = ""
    ) -> AsyncIterator[events.Event]:
        """跑一个回合，产出事件流（async generator，直接喂给 SSE）。"""
        self._ensure_tools()
        run_id = events.new_run_id()
        started = time.time()
        session.cancel.clear()
        system_prompt = build_system_prompt(session, describe_tools(), system_extra)
        session.add_user(user_message)
        # 左侧历史栏需要一个能认出来的名字：没起标题就用第一句话当标题
        if not session.title:
            session.title = session.auto_title()
            session.subject = session.subject or ""
            session.textbook = session.textbook or ""
            session.save()

        queue: "asyncio.Queue[Optional[events.Event]]" = asyncio.Queue()

        async def emit(event: events.Event) -> None:
            await queue.put(event)

        async def producer() -> None:
            steps = 0
            reason = "done"
            try:
                first = self.configs[0]
                await emit(events.run_start(run_id, session.id, first.model, first.provider))
                messages = session.history(system_prompt)
                last_exc: Optional[Exception] = None
                for idx, cfg in enumerate(self.configs):
                    if session.cancel.is_set():
                        reason = "cancelled"
                        break
                    llm = build_llm(cfg)
                    try:
                        steps, reason = await self._loop(llm, cfg, session, messages, emit)
                        last_exc = None
                        break
                    except LLMError as exc:
                        last_exc = exc
                        # 只有「这一步还什么都没产生」时换链路才安全（否则学生会看到重复内容）
                        if steps == 0 and idx + 1 < len(self.configs):
                            await emit(
                                events.notice(
                                    f"本地模型没接上（{exc}），先切到云端继续。", level="warning"
                                )
                            )
                            continue
                        raise
                if last_exc is not None:
                    await emit(events.error(f"模型链路都不可用：{last_exc}"))
                    reason = "error"
            except asyncio.CancelledError:
                reason = "cancelled"
            except Exception as exc:  # noqa: BLE001 - 兜住一切，保证事件流一定收尾
                await emit(events.error(f"{type(exc).__name__}: {exc}", traceback.format_exc()[-1500:]))
                reason = "error"
            finally:
                try:
                    session.save()
                except Exception:
                    pass
                # 归档兜底：模型这一轮没有自己调 file_conversation 的话，
                # 后台用一次很短的判断把这段对话放进该去的文件夹（失败就当无事发生）。
                if reason in ("done", "empty"):
                    try:
                        from .filing import maybe_autofile
                        info = await maybe_autofile(session)
                        if info and info.get("applied"):
                            await emit(events.notice(
                                f"已自动归档到「{info['subject']} / {info.get('textbook') or '未指定教材'}」"
                                + (f"：{info['reason']}" if info.get("reason") else "")))
                    except Exception:
                        pass
                await emit(events.run_end(run_id, reason, steps, int((time.time() - started) * 1000)))
                await queue.put(None)

        task = asyncio.create_task(producer())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ------------------------------------------------------------------
    async def _loop(
        self,
        llm: BaseLLM,
        cfg: LLMConfig,
        session: Any,
        messages: List[Dict[str, Any]],
        emit,
    ) -> Tuple[int, str]:
        """单个链路内的多步工具循环。返回 (步数, 结束原因)。"""
        max_steps = int(self.settings.get("max_steps") or 12)
        tool_timeout = float(self.settings.get("tool_timeout") or 60)
        tools_schema = tools_payload(cfg.provider) or None
        previous_signature: Optional[str] = None
        nudged = False          # 「只说不做」是否已经催过一次（只催一次，防死循环）
        used_tools = False      # 本轮是否真的用过工具
        empty_retried = False   # 「模型返回空」是否已经重试过一次
        force_required = False  # 催过还不动手 → 强制要求调用工具（tool_choice=required）
        step = 0

        for step in range(1, max_steps + 1):
            if session.cancel.is_set():
                return step - 1, "cancelled"

            text_parts: List[str] = []
            calls: List[ToolCallRequest] = []

            # 最后一步不再给工具：逼模型用文字收尾，而不是无限调工具（小模型的通病）
            step_tools = None if step == max_steps else tools_schema

            async for chunk in llm.stream(messages, step_tools,
                                          tool_choice="required" if (force_required and step_tools) else None):
                if session.cancel.is_set():
                    return step - 1, "cancelled"
                if chunk.kind == "text":
                    text_parts.append(chunk.text)
                    await emit(events.text(chunk.text))
                elif chunk.kind == "thinking":
                    await emit(events.thinking(chunk.text))
                elif chunk.kind == "tool_calls":
                    calls.extend([c for c in chunk.calls if c.name])

            content = "".join(text_parts)

            if not calls:
                # 模型这一轮什么都没返回：上游抖动、内容过滤、流被切断都可能。
                # 以前这里被当成「正常结束」，于是学生看到的是一条**空回复**、
                # 而且对话历史里还留下一条空消息误导下一轮（实测踩过：用户问「讲一下9709」，
                # 先是一句「请稍等片刻」就没了，再问一次直接空回复，全程没有任何提示）。
                if not content.strip():
                    if not empty_retried and step < max_steps:
                        empty_retried = True
                        await emit(events.notice("模型这一轮没返回内容，正在重试一次…", level="warning"))
                        messages.append({
                            "role": "user",
                            "content": "（系统提示：你上一条回复是空的，学生什么都没看到。请直接回答他的问题。）",
                        })
                        previous_signature = None
                        continue
                    await emit(events.error(
                        "模型这次没有返回任何内容（可能是上游抖动或内容被拦）。"
                        "请再说一次，或者换一种问法。"
                    ))
                    return step, "empty"
                session.add_assistant(content)
                # 「只说计划不动手」兜底：见文件顶部 _PROMISE_RE 的说明。
                # 只催一次；催过之后无论它怎么答都收尾，避免把回合拖成死循环。
                if (
                    not nudged
                    and not used_tools
                    and step < max_steps
                    and looks_like_promise(content)
                ):
                    nudged = True
                    await emit(events.notice("它只说计划没动手，已自动要求立刻执行。", level="warning"))
                    # 关键：催促只进「本次请求的消息列表」，**绝不能写进会话历史**。
                    # 踩过的坑：以前这里同时 append 到了 session.messages，
                    # 于是历史里多了一条"学生说"的内部提示，模型下一轮开始跟它较劲、
                    # 复述「检测到你写占位文字…」，彻底不干活（用户看到的就是"卡住了在打转"）。
                    messages.append({"role": "user", "content": NUDGE_MESSAGE})
                    previous_signature = None
                    continue
                if nudged and not force_required and used_tools is False and step < max_steps:
                    # 催了一次还只说不做 → 升级为**强制调用工具**（OpenAI 兼容链路支持
                    # tool_choice="required"；Ollama 不支持时会忽略，但文字指令也有用）
                    force_required = True
                    await emit(events.notice("它仍然只说不做，已强制要求必须调用工具。", level="warning"))
                    messages.append({
                        "role": "user",
                        "content": "现在必须调用一个工具，不允许只输出文字。",
                    })
                    previous_signature = None
                    continue
                return step, "done"

            used_tools = True
            force_required = False        # 已经动手了，不用再强制
            call_dicts = [{"id": c.id, "name": c.name, "args": c.args} for c in calls]
            session.add_assistant(content, call_dicts)
            # 关键：把这一步的 assistant + 工具结果**同步进本次请求的消息列表**，
            # 否则模型下一次看到的还是旧输入，会一遍遍重复调用同一个工具。
            messages.append({"role": "assistant", "content": content, "tool_calls": call_dicts})

            # 同一个工具 + 同一组参数连续出现 → 小模型死循环，直接点破
            signature = "|".join(f"{c.name}:{c.args}" for c in calls)
            if signature == previous_signature:
                await emit(events.notice("模型重复调用了同一个工具，已打断这次调用。", level="warning"))
                for c in calls:
                    note = (
                        "刚刚已经用完全相同的参数调用过这个工具了，结果就在上一条 tool 消息里。"
                        "请不要重复调用，直接基于已有结果继续，或用文字回答学生。"
                    )
                    session.add_tool(c.id, c.name, note)
                    messages.append({"role": "tool", "tool_call_id": c.id, "name": c.name, "content": note})
                previous_signature = signature
                continue
            previous_signature = signature

            for c in calls:
                await emit(events.tool_call(c.id, c.name, c.args, step))

            results = await self._execute(session, calls, step, emit, tool_timeout)
            for call, result in zip(calls, results):
                content_for_model = result.summary or ("执行成功" if result.ok else "执行失败")
                session.add_tool(call.id, call.name, content_for_model)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": content_for_model,
                    }
                )

        return max_steps, "max_steps"

    # ------------------------------------------------------------------
    async def _execute(
        self,
        session: Any,
        calls: List[ToolCallRequest],
        step: int,
        emit,
        tool_timeout: float,
    ) -> List[ToolResult]:
        """执行一批工具调用：并行安全的并发跑，其余串行。结果顺序与 calls 对齐。"""
        results: List[Optional[ToolResult]] = [None] * len(calls)

        async def run_one(index: int) -> ToolResult:
            call = calls[index]
            tool = get(call.name)
            if tool is None:
                available = ", ".join(sorted(REGISTRY))
                result = ToolResult.fail(
                    f"没有名为 `{call.name}` 的工具。可用工具：{available}。请改用正确的工具名。"
                )
                await emit(events.tool_result(call.id, call.name, False, result.summary, 0, result.error))
                return result

            args = call.args if isinstance(call.args, dict) else {}
            problem = tool.validate(args)
            started = time.time()
            if problem:
                result = ToolResult.fail(f"参数不对：{problem}")
            else:
                ctx = ToolContext(session=session, call_id=call.id, step=step, username=session.username, _emit=emit)
                # 工具可以声明自己的超时（如备课要跑几分钟），没声明才用全局值
                limit = float(tool.timeout or tool_timeout)
                try:
                    result = await asyncio.wait_for(tool.call(ctx, args), timeout=limit)
                except asyncio.TimeoutError:
                    result = ToolResult.fail(f"工具 `{call.name}` 执行超过 {limit:.0f} 秒，已中断。")
                except Exception as exc:  # noqa: BLE001 - 工具异常不该炸掉整轮对话
                    result = ToolResult.fail(
                        f"工具 `{call.name}` 执行出错：{type(exc).__name__}: {exc}",
                        error=traceback.format_exc()[-1200:],
                    )

            ms = int((time.time() - started) * 1000)
            # 富数据只走事件流；模型只看到 summary
            if result.ui:
                # 顺手把最近一次界面存进会话：切回这个对话时，工作台能恢复成原样
                try:
                    session.meta["last_ui"] = result.ui
                except Exception:
                    pass
                await emit(events.ui(result.ui))
            for art in result.artifacts or []:
                await emit(
                    events.artifact(
                        art.get("path", ""),
                        art.get("name", ""),
                        art.get("kind", "file"),
                        art.get("url", ""),
                        int(art.get("bytes") or 0),
                    )
                )
            if result.stage:
                await emit(
                    events.stage(
                        result.stage.get("emotion"), result.stage.get("motion"), result.stage.get("speak")
                    )
                )
            await emit(events.tool_result(call.id, call.name, result.ok, result.summary, ms, result.error))
            return result

        parallel = [i for i, c in enumerate(calls) if (t := get(c.name)) is not None and t.parallel_safe]
        serial = [i for i, c in enumerate(calls) if i not in parallel]

        if parallel:
            semaphore = asyncio.Semaphore(TOOL_CONCURRENCY)

            async def guarded(i: int) -> ToolResult:
                async with semaphore:
                    return await run_one(i)

            gathered = await asyncio.gather(*(guarded(i) for i in parallel))
            for i, res in zip(parallel, gathered):
                results[i] = res
        for i in serial:
            results[i] = await run_one(i)

        return [r if r is not None else ToolResult.fail("工具未执行") for r in results]


def cancel(session: Any) -> None:
    """请求取消当前回合（学生中途插话 / 关闭页面时调用）。"""
    try:
        session.cancel.set()
    except Exception:
        pass
