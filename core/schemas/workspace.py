"""core.schemas.workspace — workspace 系 EP の入力スキーマ。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_OPEN = ConfigDict(extra="allow", str_strip_whitespace=True)


class CreateWorkspaceBody(BaseModel):
    """POST /api/workspaces"""

    model_config = _OPEN
    name: str
    source_ids: list[str] = Field(default_factory=list)
    user_ids: list[str] = Field(default_factory=lambda: ["user-admin"])
    policy_ids: Optional[list[str]] = None
    policy_id: Optional[str] = None


class UpdateWorkspaceBody(BaseModel):
    """PATCH /api/workspaces/{ws_id}"""

    model_config = _OPEN
    name: Optional[str] = None
    description: Optional[str] = None
    source_ids: Optional[list[str]] = None
    user_ids: Optional[list[str]] = None
    sync_config: Optional[dict] = None


class WorkspaceSyncConfigBody(BaseModel):
    """PUT /api/workspaces/{ws_id}/sync-config"""

    model_config = _OPEN
    poll_interval_seconds: Optional[int] = None


class WorkspacePolicyBody(BaseModel):
    """PUT /api/workspaces/{ws_id}/policies"""

    model_config = _OPEN
    policy_ids: list[str] = Field(default_factory=list)
