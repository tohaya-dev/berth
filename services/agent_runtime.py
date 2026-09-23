"""Cynovela — AgentRuntime インターフェース定義 (P3-3)。

将来 Cynovela 内部操作をエージェントで自動化する基盤。
現在は型定義のみ・インスタンス化不可。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class Tool:
    """エージェントが使えるツールの定義。"""

    name: str
    description: str
    parameters: dict  # JSON Schema 形式
    requires_confirmation: bool = False  # 破壊的操作は True


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    call_id: str


@dataclass
class ToolResult:
    call_id: str
    result: Any
    success: bool
    error_message: Optional[str] = None


@dataclass
class AgentResult:
    final_answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    reasoning_steps: list[str] = field(default_factory=list)
    total_latency_ms: float = 0.0


class AgentRuntime(ABC):
    """エージェント実行基盤の抽象 IF。
    将来実装: CynovelaAgentRuntime。"""

    @abstractmethod
    async def run(self, task: str, tools: list[Tool]) -> AgentResult: ...

    @abstractmethod
    async def call_tool(self, tool: Tool, arguments: dict) -> ToolResult: ...

    @property
    @abstractmethod
    def available_tools(self) -> list[Tool]: ...
