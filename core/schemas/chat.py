"""core.schemas.chat — chat 系 EP の入力スキーマ。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

_OPEN = ConfigDict(extra="allow", str_strip_whitespace=True)


class ChatRequestBody(BaseModel):
    """POST /api/chat (主要 EP)。

    body.get の key を網羅: query / message / workspace_id / temperature /
    preset_id / model / session_id / style_role / preset / rag_mode / role_override
    """

    model_config = _OPEN
    query: Optional[str] = None
    message: Optional[str] = None
    workspace_id: Optional[str] = None
    temperature: Optional[float] = None
    preset_id: Optional[str] = None
    model: Optional[str] = None
    session_id: Optional[str] = None
    style_role: Optional[str] = None
    preset: Optional[str] = None
    rag_mode: Optional[str] = None
    role_override: Optional[str] = None


class ChatSummarizeBody(BaseModel):
    """POST /api/chat/summarize"""

    model_config = _OPEN
    prompt: str


class ChatCompareBody(BaseModel):
    """POST /api/chat/compare"""

    model_config = _OPEN
    question: str
    collection_a_id: Optional[str] = None
    collection_b_id: Optional[str] = None


class ChatPresetBody(BaseModel):
    """POST /api/chat/preset"""

    model_config = _OPEN
    preset: str = ""
