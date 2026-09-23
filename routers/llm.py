"""LLM provider endpoints (/api/llm/*)."""

from __future__ import annotations

import json

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db

import state as _state
from core.auth import _require_admin
from core.audit import _log_audit

router = APIRouter(tags=["llm"])


_LLM_LIST_MODELS_ALLOWLIST = {
    "http://localhost:1234",
    "http://localhost:11434",
    "http://127.0.0.1:1234",
    "http://127.0.0.1:11434",
    # fix2-B: コンテナ→ホスト橋渡し (ユーザー自身の Mac 上の LM Studio:1234 / Ollama:11434)。
    # コンテナ内 localhost は自コンテナを指すため host.containers.internal が実宛先になる。
    "http://host.containers.internal:1234",
    "http://host.containers.internal:11434",
    "https://openrouter.ai/api",
}


@router.get("/api/llm/presets", response_model=None)
def llm_presets(request: Request):
    """P6-E: 比較・切替用のLLMプリセット一覧を返す。"""
    from core.constants import COMPARE_MODEL_PRESETS

    _require_admin(request)
    out = [{"id": k, **{kk: vv for kk, vv in v.items() if kk != "api_key"}} for k, v in COMPARE_MODEL_PRESETS.items()]
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'llm.providers'").fetchone()
    finally:
        conn.close()
    custom: list = []
    if row and row["value"]:
        try:
            custom = json.loads(row["value"]) or []
        except Exception:
            custom = []
    seen = {p["id"] for p in out}
    for p in custom:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        rec = {
            "id": p["id"],
            "label": p.get("label", p["id"]),
            "provider": p.get("provider", "lmstudio"),
            "base_url": p.get("base_url", ""),
            "model": p.get("model", ""),
            "_user": True,
        }
        if p["id"] in seen:
            out = [x for x in out if x["id"] != p["id"]]
        out.append(rec)
        seen.add(p["id"])
        COMPARE_MODEL_PRESETS[p["id"]] = {
            "label": rec["label"],
            "provider": rec["provider"],
            "base_url": rec["base_url"],
            "model": rec["model"],
        }
    # fix-ragchat-modellist-effective-endpoint-20260618: RAGチャットのモデル一覧取得は
    # この presets の lmstudio_local.base_url を /api/llm/list-models に渡すが、静的 localhost:1234 は
    # コンテナ内 localhost が自コンテナを指すため不達 (0件/fetch failed)。serve 時に実効 endpoint
    # (DB settings.llm_endpoint = get_current_adapter().base_url) へ host+port+scheme+path を丸ごと置換する。
    # constants.COMPARE_MODEL_PRESETS は不変。実効値が空/未取得なら preset 原値のまま。
    # lmstudio_local 以外 (OpenRouter 等の明示URL・他ローカル preset) は一切変更しない (汎用解決は Fix2)。
    try:
        from core.llm import get_current_adapter as _gca

        _eff = (getattr(_gca(), "base_url", "") or "").strip()
        if _eff:
            for _p in out:
                if _p.get("id") == "lmstudio_local":
                    _p["base_url"] = _eff
    except Exception:
        pass
    if not (_state.app_config_obj and getattr(_state.app_config_obj, "mock", False)):
        out = [p for p in out if p.get("provider") != "mock"]
    return {"presets": out, "custom": custom}


@router.put("/api/llm/providers", response_model=None)
async def update_llm_providers(request: Request):
    """GUI修正2 #28: ユーザー登録のLLMプロバイダー一覧をDB settings.llm.providers に保存する。"""
    from core.constants import COMPARE_MODEL_PRESETS

    _require_admin(request)
    body = await parse_body_pydantic(request)
    items = body if isinstance(body, list) else (body.get("providers") if isinstance(body, dict) else None)
    if not isinstance(items, list):
        raise HTTPException(400, "list of providers required")
    cleaned: list[dict] = []
    seen_ids: set[str] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = (it.get("id") or "").strip()
        if not pid or pid in seen_ids:
            continue
        seen_ids.add(pid)
        cleaned.append(
            {
                "id": pid,
                "label": (it.get("label") or pid).strip(),
                "provider": (it.get("provider") or "lmstudio").strip(),
                "base_url": (it.get("base_url") or "").strip(),
                "model": (it.get("model") or "").strip(),
            }
        )
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) " "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("llm.providers", json.dumps(cleaned, ensure_ascii=False)),
        )
        _log_audit(conn, "llm_providers_updated", "", f"count={len(cleaned)}")
        conn.commit()
    finally:
        conn.close()
    for p in cleaned:
        COMPARE_MODEL_PRESETS[p["id"]] = {
            "label": p["label"],
            "provider": p["provider"],
            "base_url": p["base_url"],
            "model": p["model"],
        }
    return {"providers": cleaned}


@router.get("/api/llm/context-length", response_model=None)
async def llm_context_length(request: Request):
    """#09 Step B/E: 現在の LLM のコンテキスト長を返す。"""
    from llm_adapter import OpenAICompatibleAdapter

    _require_admin(request)
    conn = get_db()
    settings = {}
    try:
        for row in conn.execute("SELECT key, value FROM settings").fetchall():
            settings[row["key"]] = row["value"]
    finally:
        conn.close()
    manual = (settings.get("llm.ctx_len") or "").strip()
    if manual.isdigit():
        v = int(manual)
        if v > 0:
            return {"context_length": v, "source": "manual"}
    # _lmstudio_endpoint_from_settings 相当
    from llm_adapter import MockAdapter

    a = _state.adapter
    endpoint = settings.get("llm_endpoint") or ""
    if not endpoint:
        if isinstance(a, MockAdapter):
            endpoint = ""
        else:
            endpoint = getattr(a, "base_url", "http://localhost:1234") or "http://localhost:1234"
    model = ""
    if isinstance(a, OpenAICompatibleAdapter):
        endpoint = a.base_url
        model = a.model
    if not model:
        try:
            ok, mid = await a.has_loaded_model()
            if ok:
                model = mid
        except Exception:
            model = ""
    # cloud-metrics-fix-20260628: チャットで選択中のモデル(?model=)があればそれを優先する。
    #   OpenRouter 等はモデルごとに context_length が異なるため、保存アダプタのモデルでなく
    #   実際に使うモデルの ctx を引けるようにする（追加クエリパラメータ・後方互換）。
    _req_model = (request.query_params.get("model") or "").strip()
    if _req_model and _req_model != "auto":
        model = _req_model
    from rag import fetch_context_length as _fcl

    try:
        ctx = await _fcl(endpoint, model)
    except Exception:
        ctx = 0
    return {
        "context_length": int(ctx or 0),
        "source": "auto" if ctx else "unknown",
        "endpoint": endpoint,
        "model": model,
    }


@router.post("/api/llm/list-models", response_model=None)
async def llm_list_models(request: Request):
    """#06: 許可済みローカルエンドポイントからモデル一覧を取得する。

    Stage R5-fix P2 #18: 認証必須化 (allowlist は防御層として残す)。
    """
    from core.auth import _require_authenticated

    _require_authenticated(request)
    body = await parse_body_pydantic(request)
    base = (body or {}).get("base_url", "").strip().rstrip("/")
    # llmprovider-simplify-20260628: 入力直叩き。フォームの api_key を最優先で鍵解決に使う
    #   (適用前でも OpenRouter+入力トークンで一覧339が出る)。空のときだけ保存アダプタの鍵へフォールバック。
    _form_key = ((body or {}).get("api_key") or "").strip()
    if _form_key == "****":
        _form_key = ""
    if not base:
        raise HTTPException(400, "base_url required")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    # fix-llm-endpoint-unify-20260618: 静的 loopback allowlist (standalone 用に温存) に加え、
    # 設定済み endpoint (DB settings.llm_endpoint = get_current_adapter().base_url) のホストを
    # 動的に許可へ加える。コンテナでは host.containers.internal が通るようになる。
    # 設定値以外の任意ホストは引き続き拒否 (SSRF ガード維持)。
    _allowed = set(_LLM_LIST_MODELS_ALLOWLIST)
    try:
        from core.llm import get_current_adapter as _gca

        _eff = (getattr(_gca(), "base_url", "") or "").strip().rstrip("/")
        if _eff.endswith("/v1"):
            _eff = _eff[: -len("/v1")]
        if _eff:
            _allowed.add(_eff)
    except Exception:
        pass
    if base not in _allowed:
        raise HTTPException(400, "base_url is not in allowlist")
    # modelchat-ui-20260628 M-2: クラウド(非ローカル)エンドポイントは API キーが通ってからのみ
    #   モデル一覧を取得する。鍵未設定で OpenRouter 等の公開カタログ(339件)を垂れ流す挙動を止め、
    #   接続テスト(鍵未設定=warning)とモデル一覧取得の意味を一致させる。ローカル(LM Studio/Ollama)は
    #   従来どおり鍵不要。レスポンス形式(models/manual/error)は不変。
    _local_markers = ("localhost", "127.0.0.1", "host.containers.internal", "[::1]", "0.0.0.0", "host.docker.internal")
    _is_local = any(m in base for m in _local_markers)
    # llmprovider-simplify-20260628: 実効トークン解決を入力トークン優先に一本化する。
    #   フォーム api_key があればそれを使い (入力直叩き=未適用でも一覧が出る)、無ければ保存アダプタ
    #   (get_current_adapter) の鍵へフォールバック。宛先 base は上の allowlist (設定済み endpoint /
    #   loopback / openrouter.ai のみ) で制約済み=SSRF 不変。
    _session_key = _form_key
    if not _session_key:
        try:
            from core.llm import get_current_adapter as _gca2

            _ad = _gca2()
            if getattr(_ad, "api_key", ""):
                _session_key = _ad.api_key
        except Exception:
            _session_key = ""
    # 非ローカル (OpenRouter 等) は鍵が無いと取得しない (公開カタログ垂れ流しを止める)。
    #   ローカル (LM Studio/Ollama) は従来どおり鍵不要。
    if not _is_local and not _session_key:
        return {"models": [], "manual": True, "error": "api_key_required"}
    # 鍵があれば Authorization: Bearer で送る (入力トークンの本物の有効性も叩ける)。
    _auth_headers = {"Authorization": f"Bearer {_session_key}"} if _session_key else {}
    is_ollama = base.endswith(":11434")
    import httpx as _httpx

    _MAX_BYTES = 1024 * 1024

    async def _safe_get(client, url):
        resp = await client.get(url, headers=_auth_headers)
        body_bytes = resp.content[:_MAX_BYTES]
        return resp.status_code, body_bytes

    try:
        async with _httpx.AsyncClient(timeout=_httpx.Timeout(5.0, connect=5.0), follow_redirects=False) as client:
            if is_ollama:
                status, content = await _safe_get(client, f"{base}/api/tags")
                if 200 <= status < 300:
                    data = json.loads(content.decode("utf-8", errors="replace") or "{}")
                    models = [{"id": m.get("name", ""), "name": m.get("name", "")} for m in (data.get("models") or [])]
                    return {"models": models, "manual": False}
            status, content = await _safe_get(client, f"{base}/v1/models")
            if 200 <= status < 300:
                data = json.loads(content.decode("utf-8", errors="replace") or "{}")
                models = [{"id": (m or {}).get("id", "")} for m in (data.get("data") or [])]
                return {"models": models, "manual": False}
            return {"models": [], "error": f"HTTP {status}", "manual": True}
    except Exception:
        return {"models": [], "error": "fetch failed", "manual": True}
