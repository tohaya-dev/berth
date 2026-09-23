"""Agent endpoint (/api/agent/chat)."""

from __future__ import annotations

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db import get_db
from core.auth import _require_admin
from core.errors import api_error
from core.llm import get_current_adapter

router = APIRouter(tags=["agent"])


@router.post("/api/agent/chat", response_model=None)
async def agent_chat(request: Request):
    """Agentic RAG エンドポイント。
    body = {message, collection_ids[], preset='standard'|'hq', lang='ja'|'en'}"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    message = (body.get("message") or "").strip()
    collection_ids = body.get("collection_ids") or []
    agent_workspace_id = (body.get("workspace_id") or "").strip()
    preset = (body.get("preset") or "standard").strip().lower()
    lang = (body.get("lang") or "ja").strip().lower()
    if agent_workspace_id and collection_ids:
        conn = get_db()
        try:
            placeholders = ",".join("?" * len(collection_ids))
            valid_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM collections WHERE workspace_id=? AND id IN ({placeholders})",
                    [agent_workspace_id] + list(collection_ids),
                ).fetchall()
            ]
        finally:
            conn.close()
        if len(valid_ids) != len(collection_ids):
            raise api_error("UNAUTHORIZED_COLLECTIONS", "collection_ids contain unauthorized collections", status=403)
        collection_ids = valid_ids

    if preset == "lite":
        raise api_error("INVALID_PRESET", "Agent mode requires Standard or HQ preset.", status=400)
    if not message:
        raise api_error("MISSING_FIELDS", "message required", status=400)
    if not collection_ids:
        raise api_error("MISSING_FIELDS", "collection_ids required", status=400)

    class _LLMClient:
        async def _chat_async(self, **kwargs):
            adapter = get_current_adapter()
            messages = kwargs.get("messages") or []
            try:
                resp = await adapter.chat(
                    messages,
                    temperature=kwargs.get("temperature", 0.1),
                )
                return {"content": resp.get("content", "") if isinstance(resp, dict) else str(resp)}
            except Exception as e:
                return {"content": "", "error": str(e)}

        def chat(self, **kwargs):
            import asyncio as _a

            try:
                loop = _a.get_event_loop()
                if loop.is_running():
                    fut = _a.run_coroutine_threadsafe(self._chat_async(**kwargs), loop)
                    return fut.result(timeout=60)
            except Exception:
                pass
            return _a.run(self._chat_async(**kwargs))

    def _rag_search(collection_id: str, query: str, n_results: int = 3):
        try:
            from rag import rag_retrieve as _rr

            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT workspace_id FROM collections WHERE id = ?",
                    (collection_id,),
                ).fetchone()
            finally:
                conn.close()
            ws_id = row["workspace_id"] if row else collection_id
            hits = _rr(workspace_id=ws_id, query=query, n_results=n_results)
            return [
                {"text": getattr(h, "content_preview", "") or "", "score": getattr(h, "hybrid_score", 0.0)}
                for h in (hits or [])
            ]
        except Exception:
            return []

    try:
        from utils.agent import CynovelaAgent

        agent = CynovelaAgent(
            llm_client=_LLMClient(),
            rag_search_fn=_rag_search,
            collection_ids=collection_ids,
            lang=lang,
        )
        result = agent.run(user_query=message)
        return JSONResponse(content=result)
    except Exception as e:
        raise api_error("AGENT_FAILED", str(e), status=500)
