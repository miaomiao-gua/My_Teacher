"""Agent 基类 —— 所有 Agent 共享的接口与 LLM 调用工具。"""

from typing import Any, Dict, List, Optional


class BaseAgent:
    """Agent 基类。

    子类需实现 run(user_input, context) -> dict，
    返回结构统一为：
    {
        "agent": agent_name,
        "reply": str,            # 给用户的回复文本
        "fallback_to_chat": bool,# True 表示未接管，走主聊天流程
        "data": dict,            # 结构化数据（诊断报告 / 学习路径等）
    }
    """

    name: str = "base"
    description: str = "基础 Agent"

    def __init__(
        self,
        lesson_folder: Optional[str] = None,
        learner_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.lesson_folder = lesson_folder
        self.learner_id = learner_id
        self.config = config or {}

    # -- LLM 工具 ----------------------------------------------------------
    @staticmethod
    def llm_reply(message: str, system: str = "") -> str:
        """直接与 LLM 对话（复用 app.direct_llm_reply，懒加载避免循环导入）。"""
        try:
            from app import direct_llm_reply
            return direct_llm_reply(message, system=system) or ""
        except Exception as exc:
            print(f"[agent:{__name__}] LLM 调用失败: {exc}", flush=True)
            return ""

    # -- 响应构造 -----------------------------------------------------------
    @staticmethod
    def done(reply: str, **extra: Any) -> Dict[str, Any]:
        """构造成功响应（agent 名由路由分发器统一填写）。"""
        return {"reply": reply, "fallback_to_chat": False, "data": {}, **extra}

    def _fail(self, message: str) -> Dict[str, Any]:
        return {"agent": self.name, "reply": message, "fallback_to_chat": False, "data": {}}

    def _fallback(self) -> Dict[str, Any]:
        """放弃接管，交还主聊天流程。"""
        return {"agent": self.name, "reply": "", "fallback_to_chat": True, "data": {}}

    # -- 需要子类实现 -------------------------------------------------------
    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        raise NotImplementedError
