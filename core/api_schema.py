"""core.api_schema — Pydantic 共通スキーマ基盤。

Phase 3 Recon Agent L §1-1 で確認: routers/ 全 55 EP に BaseModel 利用が 0 件。
Stage R3 で routers/ の `await request.json()` 53 箇所を Pydantic 化する基盤を
本モジュールに置く。

Stage R1-1G: 基盤クラスと共通 validator のみ定義。各 router 固有のスキーマは
Stage R3 で個別に追加する。
"""

from __future__ import annotations

import re
from typing import Any, Generic, Optional, TypeVar

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Stage R3-fix: 汎用 Pydantic body パーサ ───────────────────


class OpenDictBody(BaseModel):
    """汎用 dict body schema (extra=allow)。R3-fix で全 EP body の Pydantic 化に使う。"""

    model_config = ConfigDict(extra="allow")


async def parse_body_pydantic(request: Request) -> dict[str, Any]:
    """body を OpenDictBody 経由で Pydantic 検証してから dict として返す。

    Stage R3-fix: routers/ の `await request.json()` を本関数経由に置き換えることで、
    Pydantic validation を発動させつつ既存ロジック (body.get(...)) を保持する。
    """
    try:
        raw = await request.json()
    except Exception:
        raw = {}
    # overnight-20260922 q2 B#75: NUL (\u0000) を含む文字列は PostgreSQL の text 列に格納できず
    # (psycopg DataError)、SQLite だけが黙って受けるため、PG 背骨では最初の SELECT ... WHERE name = ?
    # の時点で 500 になっていた。用途上 NUL が意味を持つ入力は無いので、入口で一律に 400 にする。
    if _contains_nul(raw):
        raise HTTPException(400, "入力に NUL 文字 (\\u0000) は使えません")
    if not isinstance(raw, dict):
        return {"_raw": raw}
    return OpenDictBody(**raw).model_dump()


def _contains_nul(value: Any) -> bool:
    """JSON 値のどこか (キー・文字列・入れ子) に NUL 文字が含まれるか。"""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(k) or _contains_nul(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_nul(v) for v in value)
    return False


# ─── 基底クラス ─────────────────────────────────────────────────


class BaseSchema(BaseModel):
    """すべての入力 schema の基底。未知フィールドを禁止する。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BaseResponseSchema(BaseModel):
    """すべての出力 schema の基底。未知フィールド許容（既存 EP 互換）。"""

    model_config = ConfigDict(extra="allow")


# ─── 汎用レスポンス封筒 ─────────────────────────────────────────

T = TypeVar("T")


class ApiResponse(BaseResponseSchema, Generic[T]):
    """汎用レスポンス封筒 (response_model 指定用)。

    Stage R3 で routers/ の Critical EP に段階的に適用する。
    """

    ok: bool = True
    data: Optional[T] = None
    error: Optional[str] = None


# ─── 共通 validator ─────────────────────────────────────────────

_PATH_TRAVERSAL_RE = re.compile(r"(\.\./|\.\.\\|/\.\.|\\\.\.)")
_VALID_ROLES_FROZEN: frozenset[str] = frozenset({"admin", "viewer"})


def validate_no_path_traversal(value: str) -> str:
    """パストラバーサル文字列を拒否する。文字列バリデータとして使用。"""
    if _PATH_TRAVERSAL_RE.search(value):
        raise ValueError("path traversal sequence detected")
    return value


def validate_role(value: str) -> str:
    """VALID_ROLES のいずれかでなければ ValueError。"""
    if value not in _VALID_ROLES_FROZEN:
        raise ValueError(f"invalid role: {value!r}")
    return value


def validate_http_url(value: str) -> str:
    """http(s):// で始まる URL のみ許可。"""
    if not value:
        return value
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ValueError("URL must start with http:// or https://")
    return value


# ─── サンプルスキーマ（Stage R3 で使用パターンの参考） ─────────────


class RoleAware(BaseSchema):
    """ロール指定を含む schema の基底（admin/viewer などを enum 検証）。"""

    role: str = Field(..., description="VALID_ROLES のいずれか")

    @field_validator("role")
    @classmethod
    def _check_role(cls, v: str) -> str:
        return validate_role(v)


__all__ = [
    "ApiResponse",
    "BaseResponseSchema",
    "BaseSchema",
    "RoleAware",
    "validate_http_url",
    "validate_no_path_traversal",
    "validate_role",
]
