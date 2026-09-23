"""core.schemas.admin — admin 系 EP の入力スキーマ。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict

_OPEN = ConfigDict(extra="allow", str_strip_whitespace=True)


class CreateUserBody(BaseModel):
    """POST /api/admin/users"""

    model_config = _OPEN
    username: str
    password: str
    role: str = "viewer"
    display_name: str = ""


class UpdateUserBody(BaseModel):
    """PATCH /api/admin/users/{user_id}"""

    model_config = _OPEN
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PasswordResetBody(BaseModel):
    """POST /api/admin/users/{user_id}/password"""

    model_config = _OPEN
    password: str
