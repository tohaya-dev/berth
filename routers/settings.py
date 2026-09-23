"""設定系エンドポイント。

server.py から /api/settings/* を段階的に切り出した集約ルーター。
ヘルパー (_validate_llm_endpoint / _get_reranker_top_n) と settings 専用の
mutable state (_data_sync_state) も併せて管理する。
"""

from __future__ import annotations

import os
import re

from core.api_schema import parse_body_pydantic
from fastapi import APIRouter, HTTPException, Request

from db import get_db
from core.auth import _require_admin
from core.llm import get_current_adapter

import state as _state
import vault_enc


router = APIRouter(tags=["settings"])


# ─────────────────────────────────────────────
# 設定系ヘルパー (settings 専用)
# ─────────────────────────────────────────────


def _validate_llm_endpoint(url: str) -> str:
    """STEP 4: SSRF 防止 - llm_endpoint に危険な URL を設定できないようにする"""
    if not isinstance(url, str):
        raise HTTPException(400, "llm_endpoint は文字列である必要があります")
    u = url.strip()
    if not u:
        raise HTTPException(400, "llm_endpoint が空です")
    if not u.startswith(("http://", "https://")):
        raise HTTPException(400, "llm_endpoint は http:// または https:// で始まる必要があります")
    forbidden = [
        (r"^file://", "file:// は使用できません"),
        (r"169\.254\.", "リンクローカル/メタデータアドレスは使用できません"),
        (r"metadata\.google\.internal", "GCP メタデータアドレスは使用できません"),
        (r"^https?://0\.0\.0\.0", "0.0.0.0 は使用できません"),
        (r"^https?://\[?::1?\]?(:|/|$)", "IPv6 ローカルホストは別表記で指定してください"),
        (r"\.amazonaws\.com.*169\.254", "AWS メタデータ参照は使用できません"),
    ]
    for pat, msg in forbidden:
        if re.search(pat, u, re.IGNORECASE):
            raise HTTPException(400, msg)
    return u


def _validate_ingest_roots_setting(value) -> str:
    """q3b-fix-20260922 D2: settings の `ingest.roots` は取り込み元の境界そのものである。

    正本が settings 表の1行 (routers/sources.py の _INGEST_ROOTS_KEY) に移ったことで、
    PUT /api/settings と POST /api/settings/import からも書けるようになっていた。そちらには
    POST /api/ingest-roots が持つ判定 (realpath・フォルダであること・家の下であること) が無く、
    `/` のような根を足すと POST /api/sources や /api/browse の境界が事実上消えていた。

    断るのではなく検査するのは、GET /api/settings/export の封筒がこの鍵を必ず含み、
    export → import の往復 (CLI `settings export/import`, MCP manage_settings) を保つため。
    規則は add_ingest_root と同じ:
      - JSON の配列で、各要素は host_path (文字列) を持つ dict
      - host_path は realpath してフォルダであること
      - 入れ物の外では家 ($HOME) の下、入れ物の中では /app/ingest の下であること
    通れば要素を {name, host_path(実体パス), label} に揃えた JSON 文字列を返す。
    """
    import json  # noqa: PLC0415

    from routers.sources import _browse_start_dir, _in_container, _is_inside_root  # noqa: PLC0415

    _key = "ingest.roots"
    if isinstance(value, str):
        try:
            _roots = json.loads(value) if value.strip() else []
        except Exception:
            raise HTTPException(400, f"{_key} は JSON の配列である必要があります")
    else:
        _roots = value
    if not isinstance(_roots, list):
        raise HTTPException(400, f"{_key} は JSON の配列である必要があります")
    _base = os.path.realpath("/app/ingest") if _in_container() else _browse_start_dir()
    _out: list = []
    for _r in _roots:
        if not isinstance(_r, dict) or not isinstance(_r.get("host_path"), str) or not _r["host_path"].strip():
            raise HTTPException(400, f"{_key} の要素は host_path (文字列) を持つ必要があります")
        _real = os.path.realpath(os.path.abspath(os.path.expanduser(_r["host_path"])))
        if not _is_inside_root(_real, _base):
            raise HTTPException(403, f"{_key}: この場所は取り込み元にできません: {_r['host_path']}")
        if not os.path.isdir(_real):
            raise HTTPException(400, f"{_key}: フォルダではありません: {_r['host_path']}")
        _name = _r.get("name")
        if not isinstance(_name, str) or not _name.strip():
            raise HTTPException(400, f"{_key}: name が要ります: {_r['host_path']}")
        _label = _r.get("label") if isinstance(_r.get("label"), str) and _r.get("label").strip() else None
        _out.append(
            {"name": _name, "host_path": _real, "label": _label or os.path.basename(_real.rstrip("/")) or _real}
        )
    return json.dumps(_out, ensure_ascii=False)


def _get_reranker_top_n() -> int:
    try:
        from core.config import CYNOVELA_CONFIG as _cfg

        return int((_cfg.get("reranker") or {}).get("top_n", 5) or 5)
    except Exception:
        return 5


# ─────────────────────────────────────────────
# エンドポイント
# ─────────────────────────────────────────────


@router.get("/api/settings/presets", response_model=None)
def get_settings_presets(request: Request):
    """PHASE S-1: 推奨プリセット定義を返す (フロントエンド ドロップダウン用)。"""
    _require_admin(request)
    return {
        "rag_params": [
            {
                "id": "rag_standard",
                "label": "RAG標準（事実重視）",
                "values": {"temperature": 0.1, "top_p": 0.9, "repeat_penalty": 1.1},
            },
            {
                "id": "balanced",
                "label": "バランス",
                "values": {"temperature": 0.3, "top_p": 0.95, "repeat_penalty": 1.05},
            },
            {
                "id": "creative",
                "label": "創造的回答",
                "values": {"temperature": 0.7, "top_p": 1.0, "repeat_penalty": 1.0},
            },
            {"id": "custom", "label": "カスタム", "values": {}},
        ],
        "chunking": [
            # PHASE UI-5: 12 種のパイプラインプリセット (rag_mode は preset と連動)
            {
                "id": "tech_manual",
                "label": "📖 技術マニュアル",
                "values": {
                    "child_chunk_size": 512,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 64,
                    "bm25_weight": 0.45,
                    "rag_mode": "hq",
                },
            },
            {
                "id": "table_data",
                "label": "📊 表・データ資料",
                "values": {
                    "child_chunk_size": 128,
                    "parent_chunk_size": 256,
                    "child_chunk_overlap": 0,
                    "bm25_weight": 0.55,
                    "rag_mode": "lite",
                },
            },
            {
                "id": "business_doc",
                "label": "📝 ビジネス文書",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 32,
                    "bm25_weight": 0.30,
                    "rag_mode": "standard",
                },
            },
            {
                "id": "communication",
                "label": "💬 コミュニケーション",
                "values": {
                    "child_chunk_size": 128,
                    "parent_chunk_size": 512,
                    "child_chunk_overlap": 16,
                    "bm25_weight": 0.20,
                    "rag_mode": "lite",
                },
            },
            {
                "id": "code_config",
                "label": "💻 コード・設定ファイル",
                "values": {
                    "child_chunk_size": 512,
                    "parent_chunk_size": 2048,
                    "child_chunk_overlap": 64,
                    "bm25_weight": 0.60,
                    "rag_mode": "standard",
                },
            },
            {
                "id": "confidential",
                "label": "🔒 機密・法務文書",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 64,
                    "bm25_weight": 0.35,
                    "rag_mode": "standard",
                },
            },
            {
                "id": "transcript",
                "label": "🎙️ 書き起こし・議事録",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 64,
                    "bm25_weight": 0.20,
                    "rag_mode": "standard",
                },
            },
            {
                "id": "logfile",
                "label": "🪵 ログファイル",
                "values": {
                    "child_chunk_size": 64,
                    "parent_chunk_size": 256,
                    "child_chunk_overlap": 0,
                    "bm25_weight": 0.70,
                    "rag_mode": "lite",
                },
            },
            {
                "id": "structured",
                "label": "🗂️ 構造化データ",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 512,
                    "child_chunk_overlap": 0,
                    "bm25_weight": 0.60,
                    "rag_mode": "lite",
                },
            },
            {
                "id": "mixed",
                "label": "🌐 混在・雑多ファイル",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 32,
                    "bm25_weight": 0.40,
                    "rag_mode": "standard",
                },
            },
            {
                "id": "research_paper",
                "label": "📚 研究論文・学術文書",
                "values": {
                    "child_chunk_size": 384,
                    "parent_chunk_size": 1536,
                    "child_chunk_overlap": 48,
                    "bm25_weight": 0.35,
                    "rag_mode": "hq",
                },
            },
            {
                "id": "quickstart",
                "label": "⚡ クイックスタート",
                "values": {
                    "child_chunk_size": 256,
                    "parent_chunk_size": 1024,
                    "child_chunk_overlap": 32,
                    "bm25_weight": 0.40,
                    "rag_mode": "standard",
                },
            },
            {"id": "custom", "label": "カスタム", "values": {}},
        ],
        "rag_modes": [
            {
                "id": "lite",
                "label": "🚀 パフォーマンス",
                "flags": {
                    "mmr_enabled": False,
                    "multi_query_enabled": False,
                    "crag_enabled": False,
                    "hyde_enabled": False,
                },
            },
            {
                "id": "standard",
                "label": "⚖️ バランス",
                "flags": {
                    "mmr_enabled": True,
                    "multi_query_enabled": True,
                    "crag_enabled": True,
                    "hyde_enabled": False,
                },
            },
            {
                "id": "hq",
                "label": "🎯 品質優先",
                "flags": {"mmr_enabled": True, "multi_query_enabled": True, "crag_enabled": True, "hyde_enabled": True},
            },
        ],
        "reranker_backends": [
            {"id": "none", "label": "無効"},
            {"id": "flashrank", "label": "FlashRank (~75MB)"},
            {"id": "cross_encoder", "label": "SentenceTransformers (~550MB)"},
            {"id": "cohere", "label": "Cohere API (要APIキー)"},
            {"id": "jina", "label": "Jina API (要APIキー)"},
        ],
    }


@router.get("/api/settings/remote-access", response_model=None)
def get_remote_access_info(request: Request):
    """PHASE B-1: 現在のバインドアドレス・ポート・TailScale IP・許可サブネットを返す。"""
    from server import _detect_tailscale_ip

    _require_admin(request)
    cfg = _state.config
    info = {
        "host": cfg.host if cfg else "0.0.0.0",
        "port": cfg.port if cfg else 8765,
        "allow_tailscale": cfg.allow_tailscale if cfg else False,
        "allow_subnets": list(cfg.allow_subnet) if cfg else [],
        "tailscale_ip": _detect_tailscale_ip(),
        "active_allowlist": [str(s) for s in (_state.allowed_subnets or [])],
    }
    info["url_localhost"] = f"http://127.0.0.1:{info['port']}"
    if info["tailscale_ip"]:
        info["url_tailscale"] = f"http://{info['tailscale_ip']}:{info['port']}"
    return info


@router.get("/api/settings", response_model=None)
def get_settings(request: Request):
    _require_admin(request)
    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (接続リークで書き込みロック残留を防ぐ)
    try:
        settings = {}
        for row in conn.execute("SELECT key, value FROM settings").fetchall():
            settings[row["key"]] = row["value"]
    finally:
        conn.close()
    return settings


@router.put("/api/settings", response_model=None)
async def update_settings(request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    # STEP 4: llm_endpoint 更新時に SSRF バリデーション
    if "llm_endpoint" in body:
        body["llm_endpoint"] = _validate_llm_endpoint(body["llm_endpoint"])
    # q3b-fix-20260922 D2: 取り込み元の境界 (ingest.roots) は add_ingest_root と同じ規則で検査する
    if "ingest.roots" in body:
        body["ingest.roots"] = _validate_ingest_roots_setting(body["ingest.roots"])
    # PHASE M-2 lm_studio 拡張: 画像モード変更を即座に CYNOVELA_CONFIG に反映する
    from core.config import apply_image_setting as _apply_img

    conn = get_db()
    # connleak-fix-20260709: 例外時も必ず close する (書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        for key, value in body.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value)),
            )
            _apply_img(key, str(value))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@router.get("/api/settings/export", response_model=None)
def export_settings(request: Request):
    """DD-CYN-0143 A-1: Settings 全体の export。
    settings 表の全キーを封筒 (format/version/exported_at) つきで返す。
    画面の Beta ボタンではなく API として持つ口。CLI・MCP はこれを呼ぶ。"""
    _require_admin(request)
    conn = get_db()
    try:
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings").fetchall()}
    finally:
        conn.close()
    from datetime import datetime, timezone

    return {
        "format": "cynovela-settings",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
    }


@router.post("/api/settings/import", response_model=None)
async def import_settings(request: Request):
    """DD-CYN-0143 A-1: Settings 全体の import。export の封筒をそのまま受ける。
    llm_endpoint は PUT /api/settings と同じ SSRF 検証を、ingest.roots は同じ境界検証を通す。"""
    _require_admin(request)
    body = await parse_body_pydantic(request)
    if not isinstance(body, dict) or not isinstance(body.get("settings"), dict):
        raise HTTPException(400, "settings object required")
    if body.get("format") not in (None, "cynovela-settings"):
        raise HTTPException(400, "unknown format")
    incoming = dict(body["settings"])
    if "llm_endpoint" in incoming:
        incoming["llm_endpoint"] = _validate_llm_endpoint(incoming["llm_endpoint"])
    # q3b-fix-20260922 D2: export の封筒は ingest.roots を含むので、断らず PUT と同じ規則で検査する
    if "ingest.roots" in incoming:
        incoming["ingest.roots"] = _validate_ingest_roots_setting(incoming["ingest.roots"])
    from core.audit import _log_audit
    from core.config import apply_image_setting as _apply_img

    conn = get_db()
    try:
        for key, value in incoming.items():
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (str(key), str(value)),
            )
            _apply_img(str(key), str(value))
        _log_audit(conn, "settings_imported", "", f"{len(incoming)} keys")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "imported": len(incoming)}


@router.get("/api/settings/models", response_model=None)
async def list_models(request: Request):
    _require_admin(request)
    models = await get_current_adapter().list_models()
    return {"data": models}


@router.get("/api/settings/system-prompt", response_model=None)
def get_system_prompt(request: Request):
    from server import _get_effective_system_prompt, DEFAULT_SYSTEM_PROMPT

    _require_admin(request)
    value = _get_effective_system_prompt()
    return {"value": value, "is_default": value == DEFAULT_SYSTEM_PROMPT}


@router.post("/api/settings/system-prompt", response_model=None)
async def save_system_prompt(request: Request):
    from server import DEFAULT_SYSTEM_PROMPT

    _require_admin(request)
    body = await parse_body_pydantic(request)
    value = body.get("value")
    conn = get_db()
    # connleak-fix-20260709: 分岐ごとの手動 close を try/finally に一本化する
    # (例外時の close 漏れ = 書き込み txn 残留 = "database is locked" を防ぐ)
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            # 空文字または null なら削除（=デフォルトにリセット）
            conn.execute("DELETE FROM settings WHERE key = ?", ("system_prompt",))
            conn.commit()
            return {"value": DEFAULT_SYSTEM_PROMPT, "is_default": True}
        if not isinstance(value, str):
            raise HTTPException(400, "value は文字列である必要があります")
        if "{context}" not in value:
            raise HTTPException(400, "システムプロンプトには {context} プレースホルダーが必要です")
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            ("system_prompt", value),
        )
        conn.commit()
    finally:
        conn.close()
    return {"value": value, "is_default": value == DEFAULT_SYSTEM_PROMPT}


@router.post("/api/settings/test-connection", response_model=None)
async def test_connection(request: Request):
    _require_admin(request)
    # llmprovider-simplify-20260628: 接続テストは保存値ではなく画面の入力値 (provider/base_url/api_key/model)
    #   で一時アダプタを作って叩く。適用前でも OpenRouter+入力トークンで本物の接続テストができ、
    #   「テスト緑なのに一覧だけ空」(保存 LM Studio を叩いていた非対称) を解消する。
    #   一時アダプタは _server._adapter / _state.adapter に代入しない (保存値を汚さない=未適用を保つ)。
    #   ボディに base_url / provider が無い (空 {} など) ときだけ従来どおり保存アダプタにフォールバック。
    try:
        body = await parse_body_pydantic(request)
    except Exception:
        body = None
    if isinstance(body, dict) and (body.get("base_url") or body.get("provider")):
        from server import get_llm_adapter

        provider = body.get("provider", "lmstudio")
        base_url = _validate_llm_endpoint(body.get("base_url", "http://localhost:1234"))
        model = body.get("model", "") or ""
        api_key = body.get("api_key", "") or ""
        if api_key == "****":
            api_key = ""
        # クラウド (openai_compat) で鍵欄が空 (マスク '****' 未編集) のときは、保存済み暗号トークンを
        #   復号して当回のテストに使う (適用後の再テストでも緑になる)。適用経路と同じ vault_enc。
        if provider == "openai_compat" and not api_key:
            try:
                _c0 = get_db()
                try:
                    _r0 = _c0.execute("SELECT value FROM settings WHERE key = 'llm_api_key_enc'").fetchone()
                finally:
                    _c0.close()
                if _r0 and _r0[0]:
                    api_key = vault_enc.dec_raw(_r0[0]) or ""
            except Exception:
                pass
        adapter = get_llm_adapter(base_url=base_url, provider=provider, model=model, api_key=api_key)
        result = await adapter.test_connection()
        # 実際に叩いた宛先を返却に含める (実宛先 openrouter かどうかを画面で確認できるように)。
        try:
            if isinstance(result, dict):
                result.setdefault("endpoint", f"{base_url}/v1")
        except Exception:
            pass
        return result
    return await get_current_adapter().test_connection()


@router.get("/api/settings/llm", response_model=None)
def get_llm_settings(request: Request):
    """現在のLLM設定を返す。api_key は値ではなく is_set フラグのみ。"""
    import server as _server
    from server import MockAdapter, OpenAICompatibleAdapter
    # provider-default-url-20260627: コンテナ対応の既定 Base URL を単一定義から取得し、追加フィールド
    #   default_base_url で返す。フロントのプロバイダー選択時の自動入力(B-2)が二重ハードコードせず共有する。
    from core.llm import default_llm_endpoint as _dle
    _default_base_url = _dle()

    _require_admin(request)
    a = get_current_adapter()
    if isinstance(a, MockAdapter):
        return {"provider": "mock", "base_url": "mock://localhost", "model": "mock-model", "api_key_set": False, "default_base_url": _default_base_url}
    if isinstance(a, OpenAICompatibleAdapter):
        return {
            "provider": getattr(a, "provider", "") or "openai_compat",
            "base_url": a.base_url,
            "model": a.model,
            "api_key_set": bool(a.api_key),
            "default_base_url": _default_base_url,
        }
    # fix-llm-endpoint-unify-20260618: 起動時キャッシュ _server._adapter ではなく、
    # 実効 endpoint (DB settings.llm_endpoint = get_current_adapter().base_url) を表示し、
    # Base URL 表示 (②) を接続テスト/モデル一覧と同じ実効値に一本化する (コンテナでは host.containers.internal)。
    try:
        from core.llm import get_current_adapter as _gca

        base_url = getattr(_gca(), "base_url", None) or getattr(a, "base_url", "http://localhost:1234")
    except Exception:
        base_url = getattr(a, "base_url", "http://localhost:1234")
    # fix061 A2: LM Studio adapter の現在モデル名を返す (空文字列回避)。
    # 優先順: adapter.model 属性 → settings DB の llm_model → "auto" 既定値。
    _model = getattr(a, "model", None)
    if not _model:
        try:
            _conn = get_db()
            # connleak-fix-20260709: 例外時も必ず close する (外側 except は握り潰しのため内側で保証)
            try:
                _row = _conn.execute("SELECT value FROM settings WHERE key = ?", ("llm_model",)).fetchone()
            finally:
                _conn.close()
            _model = _row["value"] if _row and _row["value"] else "auto"
        except Exception:
            _model = "auto"
    # fix2-A: 永続化された provider を DB から反映 (再起動後も保持を表示)。
    #         in-session の openai_compat 等は上の早期 return で拾われ、再起動後はこの分岐で DB を読む。
    # fix2-v4-A: api_key は DB/環境変数に保存しないため、有無は現在の RAM 上 adapter からのみ判定する
    #         (セッション限定: 再起動後は未設定表示になるのが正しい挙動)。
    _provider = "lmstudio"
    _api_key_set = bool(getattr(_state.adapter, "api_key", ""))
    # token-persist-fix-20260628: 再起動後 _state.adapter に鍵が無くても、保存済み暗号トークン
    #   (llm_api_key_enc) があれば「設定済み」を正直に返す (生鍵は返さない・有無のみ)。
    if not _api_key_set:
        try:
            _cg = get_db()
            try:
                _rg = _cg.execute("SELECT value FROM settings WHERE key = 'llm_api_key_enc'").fetchone()
            finally:
                _cg.close()
            _api_key_set = bool(_rg and _rg[0])
        except Exception:
            pass
    try:
        _conn2 = get_db()
        # connleak-fix-20260709: 例外時も必ず close する (同関数上方の _cg と同型の内側 try/finally)
        try:
            _rows2 = {
                r["key"]: r["value"]
                for r in _conn2.execute(
                    "SELECT key, value FROM settings WHERE key = 'llm_provider'"
                ).fetchall()
            }
        finally:
            _conn2.close()
        _provider = _rows2.get("llm_provider") or "lmstudio"
    except Exception:
        pass
    return {"provider": _provider, "base_url": base_url, "model": _model, "api_key_set": _api_key_set, "default_base_url": _default_base_url}


@router.get("/api/settings/reranker", response_model=None)
def get_reranker_settings(request: Request):
    from server import get_reranker_provider_current

    _require_admin(request)
    p = get_reranker_provider_current()
    cls = type(p).__name__
    info = {
        "provider": "none",
        "model": getattr(p, "model_name", getattr(p, "model", "")),
        "base_url": getattr(p, "base_url", ""),
        "api_key_set": bool(getattr(p, "api_key", "")),
        "top_n": _get_reranker_top_n(),
    }
    if cls == "CrossEncoderReranker":
        info["provider"] = "cross_encoder"
    elif cls == "MLXReranker":
        info["provider"] = "mlx"
    elif cls == "OllamaReranker":
        info["provider"] = "ollama"
    elif cls == "CohereReranker":
        info["provider"] = "cohere"
    elif cls == "JinaReranker":
        info["provider"] = "jina"
    elif cls == "VoyageReranker":
        info["provider"] = "voyage"
    elif cls == "OpenAICompatibleReranker":
        info["provider"] = "openai_compat"
        info["base_url"] = getattr(p, "api_url", "").replace("/v1/rerank", "")
    elif cls == "HttpReranker":
        info["provider"] = "http_tei"
        info["base_url"] = getattr(p, "endpoint", "")
    elif cls == "ExternalAcceleratorReranker":
        info["provider"] = "external_accelerator"
    # ga-finish-20260727: 再ランクの実行場所と退避状態を返す (埋め込みの mas-status と同型)。
    #   execution: external (外の口) / in_process (本体内) / none (再ランクなし)
    #   fallback: 外の口へ届かず退避が起きているか (target = 実際の退避経路)
    try:
        from providers.reranker import get_rerank_fallback_state as _grfs

        info["fallback"] = _grfs()
    except Exception:
        info["fallback"] = {"active": False}
    if info["provider"] == "external_accelerator":
        _fb = info.get("fallback") or {}
        if _fb.get("active"):
            info["execution"] = "in_process" if "in-process" in (_fb.get("target") or "") else "none"
        else:
            info["execution"] = "external"
        if info.get("base_url"):
            try:
                import httpx as _httpx

                _hr = _httpx.get(f"{info['base_url']}/health", timeout=2.0)
                info["accelerator"] = {"reachable": _hr.status_code == 200}
                if _hr.status_code == 200:
                    try:
                        info["accelerator"]["detail"] = _hr.json()
                    except Exception:
                        pass
            except Exception as _hex:
                info["accelerator"] = {"reachable": False, "error": str(_hex)}
    elif info["provider"] == "none":
        info["execution"] = "none"
    else:
        info["execution"] = "in_process"
    return info


@router.post("/api/settings/reranker", response_model=None)
async def update_reranker_settings(request: Request):
    from server import get_reranker_provider, set_reranker_provider

    _require_admin(request)
    body = await parse_body_pydantic(request)
    cfg = {
        "reranker": {
            "provider": body.get("provider", "none"),
            "model": body.get("model", ""),
            "base_url": body.get("base_url", ""),
            # ga-finish-20260727: 外の口 (external/external_accelerator) への切替は埋め込みと
            # 同じ device キーで受ける (provider=external_accelerator も同義)。
            "device": body.get("device", ""),
            "api_key": body.get("api_key", "") or os.environ.get("CYNOVELA_RERANKER_API_KEY", ""),
            "top_n": int(body.get("top_n", 5) or 5),
        }
    }
    new = get_reranker_provider(cfg)
    set_reranker_provider(new)
    # Phase F: メモリ上の CYNOVELA_CONFIG にも反映（プロセス内永続化）
    try:
        from core.config import CYNOVELA_CONFIG as _cfg

        _cfg.setdefault("reranker", {}).update(cfg["reranker"])
    except Exception:
        pass
    return {
        "ok": True,
        "provider": cfg["reranker"]["provider"],
        "model": cfg["reranker"]["model"],
        "top_n": cfg["reranker"]["top_n"],
    }


@router.post("/api/settings/reranker/test", response_model=None)
async def test_reranker_connection(request: Request):
    """Stage R5-fix P1 #15: admin 限定."""
    from core.auth import _require_admin
    from server import get_reranker_provider_current

    _require_admin(request)
    return await get_reranker_provider_current().test_connection()


@router.get("/api/settings/classifier", response_model=None)
def get_classifier_settings(request: Request):
    """Stage R5-fix P1 #15: admin 限定."""
    from core.auth import _require_admin

    _require_admin(request)
    import server as _server
    from server import APIClassifier

    p = _server._classifier
    if isinstance(p, APIClassifier):
        return {"provider": "api", "api_url": p.api_url, "api_key_set": bool(p.api_key)}
    return {"provider": "rule_based", "api_key_set": False}


@router.post("/api/settings/classifier", response_model=None)
async def update_classifier_settings(request: Request):
    from core.auth import _require_admin

    _require_admin(request)
    import server as _server
    from server import get_classifier_provider

    body = await parse_body_pydantic(request)
    cfg = {
        "classifier": {
            "provider": body.get("provider", "rule_based"),
            "api_url": body.get("api_url", ""),
            "api_key": body.get("api_key", "") or os.environ.get("CYNOVELA_CLASSIFIER_API_KEY", ""),
        }
    }
    _server._classifier = get_classifier_provider(cfg)
    return {"ok": True, "provider": cfg["classifier"]["provider"]}


@router.get("/api/settings/pii-mode", response_model=None)
def get_pii_mode(request: Request):
    """Stage R5-fix P1 #15: admin 限定."""
    from core.auth import _require_admin

    _require_admin(request)
    try:
        from utils.metadata.pii import get_pii_detection_mode

        return {"mode": get_pii_detection_mode()}
    except Exception:
        # フォールバック: yamlから直接読む
        try:
            import yaml as _yaml
            from pathlib import Path as _P

            _yaml_path = _P(__file__).resolve().parent.parent / "cynovela.yaml"
            with open(_yaml_path, "r", encoding="utf-8") as f:
                _cfg = _yaml.safe_load(f) or {}
            return {"mode": _cfg.get("pii_mode", "standard")}
        except Exception:
            return {"mode": "standard"}


def _persist_pii_mode_to_yaml(yaml_path, mode: str) -> None:
    """C-3: cynovela.yaml の pii_mode 行だけをテキスト置換で書き換える。

    従来は safe_load→safe_dump で全体を書き戻しており、コメント41行・引用符・
    キー順序が全滅していた。pii_mode はトップレベルキーであることを確認済みなので、
    行単位の正規表現置換 (^pii_mode:\\s*.*$) に限定し、他の行は 1 バイトも触らない。
    行が無ければ末尾に追記する。失敗時は例外を上げ、呼出側が応答へ warning を載せる。
    """
    with open(yaml_path, "r", encoding="utf-8") as f:
        _text = f.read()
    _new_line = f"pii_mode: {mode}"
    _pat = re.compile(r"^pii_mode:\s*.*$", re.MULTILINE)
    if _pat.search(_text):
        _text = _pat.sub(_new_line, _text, count=1)
    else:
        # 末尾に追記 (改行で終わっていなければ補う)
        if _text and not _text.endswith("\n"):
            _text += "\n"
        _text += _new_line + "\n"
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(_text)


@router.put("/api/settings/pii-mode", response_model=None)
async def set_pii_mode(request: Request):
    from server import logger

    _require_admin(request)
    body = await parse_body_pydantic(request)
    mode = (body.get("mode") or "standard").strip()
    if mode not in ("lite", "standard", "quality"):
        raise HTTPException(400, "mode は lite / standard / quality のいずれか")
    try:
        from utils.metadata.pii import set_pii_detection_mode

        set_pii_detection_mode(mode)
        # cynovela.yaml の pii_mode キーに永続化（次回起動でも反映）
        # C-3: 全体 dump をやめ、pii_mode 行だけの置換に変更 (コメント・引用符・順序を保つ)。
        #      失敗は握りつぶさず yaml_persisted=false + warning で応答に明示する
        #      (プロセス内の値は既に更新済みのため HTTP 自体は成功のまま)。
        yaml_persisted = True
        yaml_warning = None
        try:
            from pathlib import Path as _P

            _yaml_path = _P(__file__).resolve().parent.parent / "cynovela.yaml"
            if _yaml_path.exists():
                _persist_pii_mode_to_yaml(_yaml_path, mode)
            else:
                # yaml 不在時は永続化していない事実をそのまま返す (現行どおり例外にはしない)
                yaml_persisted = False
                yaml_warning = "cynovela.yaml が見つからないため永続化していません"
        except Exception as _ye:
            logger.warning(f"pii_mode yaml persist failed (continue): {_ye}")
            yaml_persisted = False
            yaml_warning = f"cynovela.yaml への永続化に失敗 (次回起動で戻る可能性): {_ye}"
        out = {"mode": mode, "status": "ok", "yaml_persisted": yaml_persisted}
        if yaml_warning:
            out["warning"] = yaml_warning
        return out
    except Exception as e:
        raise HTTPException(500, f"mode設定失敗: {e}")


@router.get("/api/settings/vector-store", response_model=None)
def get_vector_store_settings(request: Request):
    import server as _server
    from server import QdrantVectorStore

    _require_admin(request)
    p = _server._vector_store
    if isinstance(p, QdrantVectorStore):
        return {"provider": "qdrant", "url": p.url, "api_key_set": bool(p.api_key)}
    return {"provider": "chromadb", "path": getattr(p, "path", "")}


@router.post("/api/settings/vector-store", response_model=None)
async def update_vector_store_settings(request: Request):
    import server as _server
    from server import get_vector_store_provider

    _require_admin(request)
    body = await parse_body_pydantic(request)
    provider = body.get("provider", "chromadb")
    cfg = {
        "vector_store": {
            "provider": provider,
            "path": body.get("path", ""),
            "qdrant_url": body.get("qdrant_url", "http://localhost:6333"),
            "qdrant_api_key": body.get("qdrant_api_key", "") or os.environ.get("CYNOVELA_QDRANT_API_KEY", ""),
        }
    }
    _server._vector_store = get_vector_store_provider(cfg)
    return {"ok": True, "provider": provider, "warning": "既存データの再Publishが必要になります"}


@router.get("/api/settings/embedding", response_model=None)
def get_embedding_settings(request: Request):
    """現在のEmbedding Provider設定を返す。"""
    from server import get_embedding_provider_current

    _require_admin(request)
    p = get_embedding_provider_current()
    cls_name = type(p).__name__
    info = {
        "provider": "local",
        "model": getattr(p, "model_name", getattr(p, "model", "")),
        "base_url": getattr(p, "base_url", ""),
        "api_key_set": bool(getattr(p, "api_key", "")),
    }
    if cls_name == "MLXEmbeddingProvider":
        info["provider"] = "mlx"
    elif cls_name == "OpenAICompatibleEmbeddingProvider":
        info["provider"] = "openai_compat"
    # DD-CYN-0116 X-1 / G-9: 外の口の稼働状態・退避状態と、索引の埋め込み識別との
    # 整合状態を返す。画面 (governance.js / index.html) は既にこの3つの鍵を読む作りに
    # なっていたが、返す側が無く警告が一度も出せない状態だった。
    try:
        import rag as _rag

        info["fallback"] = _rag.get_embedding_fallback_state()
        info["local_device"] = getattr(_rag, "_EF_DEVICE_SELECTED", None)
        info["identity"] = _rag.check_embedding_identity()
    except Exception:
        info["fallback"] = {"active": False}
        info["local_device"] = None
        info["identity"] = {"checked": False, "match": None}
    return info


@router.post("/api/settings/embedding", response_model=None)
async def update_embedding_settings(request: Request):
    from server import get_embedding_provider, set_embedding_provider

    _require_admin(request)
    body = await parse_body_pydantic(request)
    provider = body.get("provider", "local")
    model = body.get("model", "") or ""
    base_url = body.get("base_url", "") or ""
    api_key = body.get("api_key", "") or os.environ.get("CYNOVELA_EMBEDDING_API_KEY", "")
    cfg = {
        "embedding": {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "api_key": api_key,
        }
    }
    new_provider = get_embedding_provider(cfg)
    set_embedding_provider(new_provider)
    return {
        "ok": True,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key_set": bool(api_key),
        "warning": "既存ChromaDB Collectionは旧モデルで埋め込み済みです。再Publishを推奨します。",
    }


# ─────────────────────────────────────────────
# Chunk 3: LLM(POST) / DataSync
# ─────────────────────────────────────────────


@router.post("/api/settings/llm", response_model=None)
async def update_llm_settings(request: Request):
    """LLM設定を動的更新。api_key はフォーム入力のみ (このセッションのRAM上の adapter に保持し、
    DB・設定ファイル・環境変数には一切保存しない)。"""
    import server as _server
    from server import get_llm_adapter

    _require_admin(request)
    body = await parse_body_pydantic(request)
    provider = body.get("provider", "lmstudio")
    base_url = body.get("base_url", "http://localhost:1234")
    # STEP 4: SSRF 防止
    base_url = _validate_llm_endpoint(base_url)
    model = body.get("model", "") or ""
    # fix2-v4-A: api_key はフォーム入力のみ (環境変数からは参照しない・セッション限定)。
    api_key = body.get("api_key", "") or ""
    # token-persist-fix-20260628: api_key 欄を送ったか否かで「設定/削除」と「保持」を分ける。
    _key_provided = "api_key" in body
    if provider == "openai_compat" and not _key_provided:
        # 未送信=保持。保存済み暗号トークンを復号して当セッション adapter に載せ直す
        #   (model 等だけ変えて適用したとき RAM 上トークンを落とさないため)。
        try:
            _c0 = get_db()
            try:
                _r0 = _c0.execute("SELECT value FROM settings WHERE key = 'llm_api_key_enc'").fetchone()
            finally:
                _c0.close()
            if _r0 and _r0[0]:
                api_key = vault_enc.dec_raw(_r0[0]) or ""
        except Exception:
            pass
    new_adapter = get_llm_adapter(
        base_url=base_url,
        provider=provider,
        model=model,
        api_key=api_key,
    )
    _server._adapter = new_adapter
    _state.adapter = new_adapter  # state との同期（既存バグ修正）
    # F1: get_current_adapter() / chat 経路が参照する DB キー (llm_endpoint / llm_model) に永続化する。
    #     従来 POST は DB を更新しておらず、LMStudio 型アダプターでは次の chat 要求で
    #     get_current_adapter() が settings.llm_endpoint を読み直して巻き戻していたため、
    #     プロバイダー切替（例: LM Studio → Ollama）が実効反映されなかった。
    _final_key_set = False
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("llm_endpoint", base_url),
        )
        if model:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("llm_model", model),
            )
        # fix2-A: provider を DB に永続化 (従来 RAM のみ→再起動で LM Studio に縮退していた)。
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            ("llm_provider", provider or "lmstudio"),
        )
        # fix2-v4-A: api_key は DB に永続化しない (セッション限定: RAM 上の adapter にのみ保持)。
        #         万一以前の版が保存していた llm_api_key が残っていれば併せて掃除する。
        conn.execute("DELETE FROM settings WHERE key = 'llm_api_key'")
        # token-persist-fix-20260628: クラウド(openai_compat=OpenRouter)のトークンは既存金庫で
        #   暗号化し llm_api_key_enc に1行保存(平文は保存しない)。空送信=削除/provider切替=削除。
        if provider == "openai_compat":
            if _key_provided:
                if api_key:
                    conn.execute(
                        "INSERT INTO settings (key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        ("llm_api_key_enc", vault_enc.enc_raw(api_key)),
                    )
                else:
                    conn.execute("DELETE FROM settings WHERE key = 'llm_api_key_enc'")
            # 未送信=保持: enc 行はそのまま
        else:
            conn.execute("DELETE FROM settings WHERE key = 'llm_api_key_enc'")
        conn.commit()
        _rf = conn.execute("SELECT value FROM settings WHERE key = 'llm_api_key_enc'").fetchone()
        _final_key_set = bool(_rf and _rf[0]) and (provider == "openai_compat")
    finally:
        conn.close()
    return {
        "ok": True,
        "provider": provider,
        "base_url": base_url,
        "model": model,
        "api_key_set": _final_key_set,
    }


# ─── BLOCK D: DataSync 制御 ───


# ─── DataSync state ───
_data_sync_state: dict = {"enabled": False, "interval_sec": 60}


@router.get("/api/settings/datasync", response_model=None)
def get_datasync_settings(request: Request):
    _require_admin(request)
    return dict(_data_sync_state)


@router.post("/api/settings/datasync", response_model=None)
async def update_datasync_settings(request: Request):
    _require_admin(request)
    body = await parse_body_pydantic(request)
    enable = bool(body.get("enabled", False))
    interval = int(body.get("interval_sec", _data_sync_state["interval_sec"]) or 60)
    _data_sync_state["enabled"] = enable
    _data_sync_state["interval_sec"] = max(10, interval)
    # 開始 / 停止
    from db import DB_PATH
    from services.data_sync import start_service, stop_service

    try:
        if enable:
            await start_service(DB_PATH, _data_sync_state["interval_sec"])
        else:
            await stop_service()
    except Exception as e:
        return {"ok": False, "error": str(e), **_data_sync_state}
    return {"ok": True, **_data_sync_state}
