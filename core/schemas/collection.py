"""core.schemas.collection — collection 系 EP の入力スキーマ。"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

_OPEN = ConfigDict(extra="allow", str_strip_whitespace=True)


class CreateCollectionBody(BaseModel):
    """POST /api/collections"""

    model_config = _OPEN
    name: str
    workspace_id: str
    file_ids: list[str] = Field(default_factory=list)
    access_level: str = "public"
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin", "viewer"])
    rag_strategy: str = "hybrid_bm25"
    # ga-finish-P4 (rawmode-receptor-close-20260727): raw_mode は伏字を迂回する受け口として
    # 廃止。入力スキーマからも外す (受理すると 400)。
    # berth 差異: 姉妹系統はここで raw_only も廃止済み (masked-only §9-7) だが、berth は
    # 当該変更を受けておらず raw_only は admin 限定の取り込みモードとして現存する。本同送の
    # 範囲は raw_mode のみで、raw_only の既存挙動には手を入れない。
    classification_filter: list[str] = Field(default_factory=list)


class UpdateCollectionBody(BaseModel):
    """PATCH /api/collections/{col_id}"""

    model_config = _OPEN
    name: Optional[str] = None
    access_level: Optional[str] = None
    allowed_roles: Optional[list[str]] = None
    rag_strategy: Optional[str] = None
    rag_mode: Optional[str] = None
    file_ids: Optional[list[str]] = None
