"""Unified models listing endpoint (/api/models)."""

from __future__ import annotations

import os

from fastapi import APIRouter, Request

from core.auth import _require_admin

router = APIRouter(tags=["models"])


@router.get("/api/models", response_model=None)
async def get_models_unified(request: Request):
    """PHASE X-4: 全 LLM プロバイダーのモデル一覧をマージして返す。"""
    _require_admin(request)
    out: list = []
    # LM Studio
    # PORTABILITY FIX: settings DB / アダプタ経由で base_url を取得（localhost ハードコード廃止）
    try:
        import httpx as _hx
        from routers.lmstudio import _lmstudio_endpoint_from_settings
        _ls = (_lmstudio_endpoint_from_settings() or "http://localhost:1234").rstrip("/")
        if _ls.endswith("/v1"):
            _ls = _ls[:-3]

        async with _hx.AsyncClient(timeout=3.0, trust_env=False) as cl:
            r = await cl.get(f"{_ls}/v1/models")
            if r.status_code == 200:
                for m in r.json().get("data") or []:
                    mid = m.get("id") or ""
                    if mid:
                        out.append({"id": mid, "provider": "lmstudio", "label": mid})
    except Exception:
        pass
    # Ollama
    # PORTABILITY FIX 20260527 Stage2 P4: cynovela.yaml の llm.base_url から取得（env var 撤去）
    # LM Studio と同じキーを共有。ユーザーは llm.provider と base_url で区別する。
    try:
        import httpx as _hx
        from llm_adapter import _get_llm_base_url_from_config
        _ol = _get_llm_base_url_from_config("http://localhost:11434").rstrip("/")

        async with _hx.AsyncClient(timeout=3.0, trust_env=False) as cl:
            r = await cl.get(f"{_ol}/api/tags")
            if r.status_code == 200:
                for m in r.json().get("models") or []:
                    mid = m.get("name") or m.get("model") or ""
                    if mid:
                        out.append({"id": mid, "provider": "ollama", "label": f"{mid} (ollama)"})
    except Exception:
        pass
    return {"models": out}
