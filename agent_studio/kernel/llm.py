"""LLM 适配层 —— 把「两种协议 + 一种降级」统一成同一个流式接口。

为什么要适配层
--------------
本地 Ollama 与云端（OpenAI 兼容）的工具调用**报文格式不同**：
- Ollama：NDJSON 流，`message.tool_calls[].function.arguments` 是**对象**
- OpenAI 兼容：SSE 流，`delta.tool_calls[].function.arguments` 是**拼接的 JSON 字符串**
如果业务层直接对着某一种写，换模型时就要改一堆地方。这里收敛成 `Chunk` 一种输出。

降级通道（重要）
----------------
不支持 function calling 的模型，或工具调用连续失败时，退化为**文本标记协议**
（`[TOOL:{"type":"calc",...}]`）——老项目已经证明小模型能稳定输出这种标记。
降级不是妥协，而是「任何模型都能用」的保险。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from .config import LLMConfig


class LLMError(RuntimeError):
    """模型链路错误（连接失败、协议错误、上游报错）。"""


@dataclass
class ToolCallRequest:
    id: str
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """统一的流式片段。"""

    kind: str                                  # text | thinking | tool_calls | done
    text: str = ""
    calls: List[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)


def _safe_json(text: Any) -> Dict[str, Any]:
    """把模型给的 arguments 解析成 dict。模型偶尔给出半个 JSON 或带 markdown 围栏。"""
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        return {}
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"```\s*$", "", raw).strip()
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {"value": obj}
    except Exception:
        # 兜底：截取第一个 { 到最后一个 }
        i, j = raw.find("{"), raw.rfind("}")
        if 0 <= i < j:
            try:
                obj = json.loads(raw[i : j + 1])
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}


# ---------------------------------------------------------------------------
# 消息格式转换：会话里存「中立格式」，发给不同 provider 时再转
# ---------------------------------------------------------------------------
def build_messages(provider: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = m["tool_calls"]
            if provider == "ollama":
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": c.get("name"),
                                    "arguments": c.get("args") or {},
                                }
                            }
                            for c in calls
                        ],
                    }
                )
            else:
                out.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {
                                "id": c.get("id") or f"call_{i}",
                                "type": "function",
                                "function": {
                                    "name": c.get("name"),
                                    "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False),
                                },
                            }
                            for i, c in enumerate(calls)
                        ],
                    }
                )
        elif role == "tool":
            if provider == "ollama":
                msg = {"role": "tool", "content": m.get("content") or ""}
                if m.get("name"):
                    msg["tool_name"] = m["name"]
                out.append(msg)
            else:
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id") or "",
                        "content": m.get("content") or "",
                    }
                )
        else:
            out.append({"role": role, "content": m.get("content") or ""})
    return out


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------
class BaseLLM:
    provider = "base"

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    @property
    def model(self) -> str:
        return self.cfg.model

    async def stream(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> AsyncIterator[Chunk]:
        raise NotImplementedError
        yield Chunk("done")  # pragma: no cover

    async def healthy(self) -> tuple[bool, str]:
        return True, ""


# ---------------------------------------------------------------------------
# Ollama（本地）
# ---------------------------------------------------------------------------
class OllamaLLM(BaseLLM):
    provider = "ollama"

    async def healthy(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.cfg.base_url}/api/tags")
                if r.status_code != 200:
                    return False, f"Ollama 返回 {r.status_code}"
                names = [m.get("name") for m in (r.json().get("models") or [])]
                if names and self.cfg.model not in names:
                    return False, f"模型 {self.cfg.model} 未安装（已有：{', '.join(names[:6])}）"
                return True, ""
        except Exception as exc:
            return False, f"连不上 Ollama（{self.cfg.base_url}）：{exc}"

    async def stream(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> AsyncIterator[Chunk]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": build_messages("ollama", messages),
            "stream": True,
            "options": {
                "temperature": self.cfg.temperature,
                "num_ctx": self.cfg.num_ctx,
                "num_predict": self.cfg.num_predict,
            },
        }
        if tools:
            payload["tools"] = tools
        if self.cfg.think is not None:
            payload["think"] = bool(self.cfg.think)

        async def _once(body: Dict[str, Any]) -> AsyncIterator[Chunk]:
            timeout = httpx.Timeout(self.cfg.timeout, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.cfg.base_url}/api/chat", json=body) as resp:
                    if resp.status_code >= 400:
                        detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                        raise LLMError(f"Ollama {resp.status_code}: {detail}")
                    async for line in resp.aiter_lines():
                        line = (line or "").strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except Exception:
                            continue
                        if obj.get("error"):
                            raise LLMError(f"Ollama: {obj['error']}")
                        msg = obj.get("message") or {}
                        thinking = msg.get("thinking")
                        if thinking:
                            yield Chunk("thinking", text=thinking)
                        content = msg.get("content")
                        if content:
                            yield Chunk("text", text=content)
                        raw_calls = msg.get("tool_calls") or []
                        if raw_calls:
                            calls: List[ToolCallRequest] = []
                            for idx, tc in enumerate(raw_calls):
                                fn = tc.get("function") or {}
                                calls.append(
                                    ToolCallRequest(
                                        id=tc.get("id") or f"call_{idx}",
                                        name=fn.get("name") or tc.get("name") or "",
                                        args=_safe_json(fn.get("arguments")),
                                    )
                                )
                            yield Chunk("tool_calls", calls=calls)
                        if obj.get("done"):
                            yield Chunk(
                                "done",
                                finish_reason=obj.get("done_reason") or "stop",
                                usage={
                                    "prompt_tokens": obj.get("prompt_eval_count"),
                                    "completion_tokens": obj.get("eval_count"),
                                },
                            )
                            return

        try:
            async for chunk in _once(payload):
                yield chunk
        except LLMError as exc:
            # 老版本 Ollama 不认 `think` 字段：去掉它重试一次，而不是把整轮对话判死
            if "think" in payload and ("think" in str(exc) or "400" in str(exc)):
                payload.pop("think", None)
                async for chunk in _once(payload):
                    yield chunk
            else:
                raise


# ---------------------------------------------------------------------------
# OpenAI 兼容（云端 / 硅基流动 / DeepSeek / vLLM …）
# ---------------------------------------------------------------------------
class OpenAICompatLLM(BaseLLM):
    provider = "openai"

    async def healthy(self) -> tuple[bool, str]:
        """真检查：拿 /models 探一次。

        为什么不只查「配置里有没有 Key」：Key 过期或写错时，只有真发一次请求才知道；
        当主力链路是云端时，这个差别就是「一上课就报错」和「上课前就发现」的区别。
        /models 不消耗 token，是最便宜的探针。
        """
        if not self.cfg.api_key:
            return False, "没有配置云端 API Key"
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    f"{self.cfg.base_url}/models",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                )
            if resp.status_code == 401:
                return False, "API Key 无效或已过期（401）"
            if resp.status_code == 403:
                return False, "API Key 没有该模型的权限（403）"
            if resp.status_code >= 400:
                return False, f"云端返回 {resp.status_code}"
            return True, ""
        except Exception as exc:  # noqa: BLE001
            return False, f"连不上云端（{self.cfg.base_url}）：{exc}"

    async def stream(
        self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[str] = None,
    ) -> AsyncIterator[Chunk]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": build_messages("openai", messages),
            "stream": True,
            "temperature": self.cfg.temperature,
        }
        if self.cfg.num_predict:
            payload["max_tokens"] = self.cfg.num_predict
        if tools:
            payload["tools"] = tools
            # tool_choice="required"：催过一次还只说不做时，强制模型必须调用工具。
            # （不是所有兼容服务都支持，支持不了的会报错 —— 所以只在"已经催过"时用。）
            payload["tool_choice"] = tool_choice or "auto"

        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"

        pending: Dict[int, Dict[str, Any]] = {}
        order: List[int] = []
        timeout = httpx.Timeout(self.cfg.timeout, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", f"{self.cfg.base_url}/chat/completions", json=payload, headers=headers
            ) as resp:
                if resp.status_code >= 400:
                    detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                    raise LLMError(f"云端 {resp.status_code}: {detail}")
                async for line in resp.aiter_lines():
                    line = (line or "").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        obj = json.loads(body)
                    except Exception:
                        continue
                    if obj.get("error"):
                        raise LLMError(f"云端: {obj['error']}")
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield Chunk("thinking", text=reasoning)
                    if delta.get("content"):
                        yield Chunk("text", text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        if idx not in pending:
                            pending[idx] = {"id": tc.get("id") or f"call_{idx}", "name": "", "args": ""}
                            order.append(idx)
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            pending[idx]["name"] = fn["name"]
                        if fn.get("arguments"):
                            pending[idx]["args"] += fn["arguments"]
                        if tc.get("id"):
                            pending[idx]["id"] = tc["id"]
                    if choice.get("finish_reason"):
                        usage = obj.get("usage") or {}
                        if pending:
                            calls = [
                                ToolCallRequest(
                                    id=pending[i]["id"],
                                    name=pending[i]["name"],
                                    args=_safe_json(pending[i]["args"]),
                                )
                                for i in sorted(order)
                            ]
                            yield Chunk("tool_calls", calls=calls)
                            pending, order = {}, []
                        yield Chunk("done", finish_reason=choice["finish_reason"], usage=usage)
                        return
        if pending:
            calls = [
                ToolCallRequest(id=pending[i]["id"], name=pending[i]["name"], args=_safe_json(pending[i]["args"]))
                for i in sorted(order)
            ]
            yield Chunk("tool_calls", calls=calls)
        yield Chunk("done", finish_reason="stop")


def build_llm(cfg: LLMConfig) -> BaseLLM:
    if cfg.provider == "openai":
        return OpenAICompatLLM(cfg)
    return OllamaLLM(cfg)


async def probe_chain(configs: List[LLMConfig]) -> List[Dict[str, Any]]:
    """探测每条链路的可用性（健康检查接口用）。"""
    out: List[Dict[str, Any]] = []
    for cfg in configs:
        llm = build_llm(cfg)
        try:
            ok, detail = await asyncio.wait_for(llm.healthy(), timeout=8)
        except Exception as exc:
            ok, detail = False, str(exc)
        out.append({"label": cfg.label, "provider": cfg.provider, "model": cfg.model, "ok": ok, "detail": detail})
    return out
