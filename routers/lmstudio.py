"""LM Studio v1 integration endpoints (/api/lmstudio/*)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

import state as _state
from core.api_schema import parse_body_pydantic
from core.auth import _require_admin

router = APIRouter(tags=["lmstudio"])


def _lmstudio_endpoint_from_settings() -> str:
    """現在の LLM settings から LM Studio エンドポイントを返す。

    fix-llm-endpoint-unify-20260618: 起動時キャッシュ _state.adapter.base_url (陳腐 localhost) ではなく
    get_current_adapter() (DB settings.llm_endpoint を都度読み直す = 実効 endpoint) の base_url を返し、
    モデル管理 (/api/lmstudio/*) も接続テストと同じ実効 endpoint に一本化する。Mock 時は従来どおり "" を返す。
    """
    from llm_adapter import MockAdapter
    from core.llm import get_current_adapter

    a = get_current_adapter()
    if isinstance(a, MockAdapter):
        return ""
    return getattr(a, "base_url", "http://localhost:1234") or "http://localhost:1234"


@router.get("/api/lmstudio/models", response_model=None)
async def lmstudio_models(request: Request):
    """LM Studio から利用可能 (ロード済み) モデル一覧を取得する。"""
    _require_admin(request)
    endpoint = _lmstudio_endpoint_from_settings()
    if not endpoint or endpoint.startswith("mock://"):
        return {"data": [], "error": "LM Studio エンドポイントが未設定 (mock モード)"}
    base = endpoint.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    try:
        import httpx as _httpx

        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{base}/v1/models")
            if 200 <= resp.status_code < 300:
                return resp.json()
            return {"data": [], "error": f"HTTP {resp.status_code}"}
    except Exception as e:
        return {"data": [], "error": str(e)}


@router.post("/api/lmstudio/load", response_model=None)
async def lmstudio_load(request: Request):
    """指定したモデルを LM Studio にロードする。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    model_name = (body or {}).get("model", "").strip()
    if not model_name:
        raise HTTPException(400, "model name required")
    endpoint = _lmstudio_endpoint_from_settings()
    if not endpoint or endpoint.startswith("mock://"):
        return {"status": "skip", "message": "LM Studio エンドポイントが未設定 (mock モード)"}
    from rag import ensure_model_loaded as _ensure

    return await _ensure(endpoint, model_name)
