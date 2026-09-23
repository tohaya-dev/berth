"""Run mode endpoint (/api/mode)."""

from __future__ import annotations

from fastapi import APIRouter, Request

import state as _state
from core.api_schema import BaseResponseSchema as _PilotResp

router = APIRouter(tags=["mode"])


@router.get("/api/mode", response_model=_PilotResp)  # FIX-052 パイロット
def get_mode(request: Request):
    """実行モードを返す。フロントがバナー表示判定に使う。

    Stage R5-fix P2 #17: 匿名は {mode} のみ。admin は endpoint / model を含む詳細を返す。
    """
    from core.auth import _require_authenticated
    from llm_adapter import MockAdapter, OpenAICompatibleAdapter

    # fix-v3 (A1-F1): JWT トークン(eyJ..) を解釈できるよう _require_authenticated 経由で
    # user を解決する。/api/mode は public(匿名=200で{mode}のみ)を維持するため認証失敗は
    # 握って user=None とする。従来は get_user_from_token 直呼びで JWT 非対応のため JWT admin が
    # viewer 相当に silent degradation し endpoint/model 詳細が返らなかった。ロール到達性は不変。
    try:
        user = _require_authenticated(request)
    except Exception:
        user = None
    is_admin = bool(user and user.get("role") == "admin")

    adapter = _state.adapter
    if isinstance(adapter, MockAdapter):
        return {"mode": "mock"}
    if isinstance(adapter, OpenAICompatibleAdapter):
        if is_admin:
            return {
                "mode": "openai_compat",
                "endpoint": f"{adapter.base_url}/v1",
                "model": adapter.model,
            }
        return {"mode": "openai_compat"}
    if _state.config is not None and _state.config.demo:
        return {"mode": "demo"}
    return {"mode": "production"}
