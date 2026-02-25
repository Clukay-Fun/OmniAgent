"""
异常处理模块

统一定义自定义异常类，便于精确捕获和处理
"""

from __future__ import annotations

from typing import Any


# ============================================
# region 基础异常
# ============================================
class OmniAgentError(Exception):
    """OmniAgent 基础异常类"""
    
    def __init__(
        self,
        message: str,
        code: str = "UNKNOWN_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
# endregion
# ============================================


# ============================================
# region Skill 相关异常
# ============================================
class SkillError(OmniAgentError):
    """技能执行异常"""
    
    def __init__(
        self,
        message: str,
        skill_name: str,
        code: str = "SKILL_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details)
        self.skill_name = skill_name
        self.details["skill_name"] = skill_name


class SkillNotFoundError(SkillError):
    """技能未找到"""
    
    def __init__(self, skill_name: str) -> None:
        super().__init__(
            message=f"技能 '{skill_name}' 未注册",
            skill_name=skill_name,
            code="SKILL_NOT_FOUND",
        )


class SkillExecutionError(SkillError):
    """技能执行失败"""
    
    def __init__(
        self,
        skill_name: str,
        cause: str,
        original_exception: Exception | None = None,
    ) -> None:
        super().__init__(
            message=f"技能 '{skill_name}' 执行失败: {cause}",
            skill_name=skill_name,
            code="SKILL_EXECUTION_ERROR",
            details={"cause": cause},
        )
        self.original_exception = original_exception


class SkillTimeoutError(SkillError):
    """技能执行超时"""

    def __init__(self, skill_name: str, timeout_seconds: float) -> None:
        super().__init__(
            message=f"技能 '{skill_name}' 执行超时 ({timeout_seconds}s)",
            skill_name=skill_name,
            code="SKILL_TIMEOUT",
            details={"timeout_seconds": timeout_seconds},
        )
# endregion
# ============================================


# ============================================
# region Intent 相关异常
# ============================================
class IntentError(OmniAgentError):
    """意图解析异常"""
    
    def __init__(
        self,
        message: str,
        code: str = "INTENT_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details)


class IntentParseError(IntentError):
    """意图解析失败"""
    
    def __init__(self, query: str, cause: str) -> None:
        super().__init__(
            message=f"无法解析意图: {cause}",
            code="INTENT_PARSE_ERROR",
            details={"query": query, "cause": cause},
        )
# endregion
# ============================================


# ============================================
# region LLM 相关异常
# ============================================
class LLMError(OmniAgentError):
    """LLM 调用异常"""
    
    def __init__(
        self,
        message: str,
        code: str = "LLM_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details)


class LLMTimeoutError(LLMError):
    """LLM 调用超时"""
    
    def __init__(self, timeout_seconds: float) -> None:
        super().__init__(
            message=f"LLM 调用超时 ({timeout_seconds}s)",
            code="LLM_TIMEOUT",
            details={"timeout_seconds": timeout_seconds},
        )


class LLMRateLimitError(LLMError):
    """LLM 速率限制"""
    
    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(
            message="LLM 调用频率超限",
            code="LLM_RATE_LIMIT",
            details={"retry_after": retry_after},
        )


class LLMResponseError(LLMError):
    """LLM 响应格式错误"""
    
    def __init__(self, cause: str) -> None:
        super().__init__(
            message=f"LLM 响应解析失败: {cause}",
            code="LLM_RESPONSE_ERROR",
            details={"cause": cause},
        )
# endregion
# ============================================


# ============================================
# region MCP 相关异常
# ============================================
class MCPError(OmniAgentError):
    """MCP 调用异常"""
    
    def __init__(
        self,
        message: str,
        code: str = "MCP_ERROR",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details)


class MCPConnectionError(MCPError):
    """MCP 连接失败"""
    
    def __init__(self, url: str, cause: str) -> None:
        super().__init__(
            message=f"无法连接 MCP Server: {cause}",
            code="MCP_CONNECTION_ERROR",
            details={"url": url, "cause": cause},
        )


class MCPTimeoutError(MCPError):
    """MCP 调用超时"""
    
    def __init__(self, tool_name: str, timeout_seconds: float) -> None:
        super().__init__(
            message=f"MCP 工具 '{tool_name}' 调用超时",
            code="MCP_TIMEOUT",
            details={"tool_name": tool_name, "timeout_seconds": timeout_seconds},
        )


class MCPToolError(MCPError):
    """MCP 工具执行失败"""
    
    def __init__(self, tool_name: str, cause: str) -> None:
        super().__init__(
            message=f"MCP 工具 '{tool_name}' 执行失败: {cause}",
            code="MCP_TOOL_ERROR",
            details={"tool_name": tool_name, "cause": cause},
        )
# endregion
# ============================================


# ============================================
# region 配置相关异常
# ============================================
class ConfigError(OmniAgentError):
    """配置错误"""
    
    def __init__(
        self,
        message: str,
        config_key: str | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code="CONFIG_ERROR",
            details={"config_key": config_key} if config_key else {},
        )


class ConfigNotFoundError(ConfigError):
    """配置文件不存在"""
    
    def __init__(self, config_path: str) -> None:
        super().__init__(
            message=f"配置文件不存在: {config_path}",
            config_key=config_path,
        )
        self.code = "CONFIG_NOT_FOUND"
# endregion
# ============================================


# ============================================
# region 用户友好错误消息
# ============================================
USER_FRIENDLY_MESSAGES = {
    "SKILL_NOT_FOUND": "抱歉，我暂时无法处理这个请求。",
    "SKILL_EXECUTION_ERROR": "处理请求时遇到问题，请稍后重试。",
    "SKILL_TIMEOUT": "抱歉，操作响应超时，请稍后重试。",
    "LLM_TIMEOUT": "响应超时，请稍后重试。",
    "LLM_RATE_LIMIT": "请求太频繁，请稍后再试。",
    "MCP_CONNECTION_ERROR": "服务暂时不可用，请稍后重试。",
    "MCP_TIMEOUT": "查询超时，请稍后重试。",
    "UNKNOWN_ERROR": "遇到未知错误，请稍后重试。",
}


def get_user_message(error: OmniAgentError) -> str:
    """获取用户友好的错误消息"""
    return USER_FRIENDLY_MESSAGES.get(error.code, USER_FRIENDLY_MESSAGES["UNKNOWN_ERROR"])
# endregion
# ============================================
