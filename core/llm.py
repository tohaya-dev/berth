"""LLM アダプター解決ヘルパー。

server.py の `_current_adapter` を切り出したモジュール。
state.adapter を起点に、LMStudioAdapter の場合のみ
settings.llm_endpoint を都度読み直す動的挙動を保持する。
"""

from __future__ import annotations

from db import get_db
from llm_adapter import get_llm_adapter, MockAdapter, OpenAICompatibleAdapter

import state as _state

import os.path as _osp


def default_llm_endpoint() -> str:
    """LLM 既定エンドポイント (単一定義: 種/既定フォールバック/フロント配布が共有)。

    コンテナ形態ではホストの LM Studio へ podman ホストゲートウェイ
    (host.containers.internal) 経由で届く必要があり、スタンドアロンでは
    localhost が正しい。判定はコンテナマーカー /run/.containerenv のみで行い、
    環境変数は一切参照しない (スタンドアロンの localhost を壊さない)。
    """
    in_container = _osp.exists("/run/.containerenv") or _osp.exists("/.dockerenv")
    host = "host.containers.internal" if in_container else "localhost"
    return f"http://{host}:1234/v1"


def get_current_adapter():
    """現在有効な LLM アダプターを返す。

    DB settings を正として provider / endpoint / model を都度確認する。
    複数 API Pod では POST /api/settings/llm が当たった Pod だけ RAM 上の
    adapter を更新するため、他 Pod の古い OpenAICompatibleAdapter をそのまま
    返すと、DB と違う backend へ RAG 生成を投げることがある。
    """
    # fix2-A: DB から provider / endpoint / model を読み、任意プロバイダーで adapter を組み立てる。
    #   従来は llm_endpoint だけ読み LM Studio へ縮退していたため、再起動 (起動時 _state.adapter が
    #   LMStudioAdapter に戻る) を跨ぐと外部プロバイダー (Ollama/OpenRouter) の provider が消えた。
    #   provider 未設定時は従来どおり lmstudio 既定。
    # token-persist-fix-20260628: クラウド(openai_compat=OpenRouter)のトークンは settings の
    #   暗号化キー llm_api_key_enc に金庫(vault_enc)経由で保存されるため、この再構築経路でも
    #   enc があれば復号して載せる(無ければ空)。in-session の外部プロバイダーは上の早期 return
    #   (RAM 保持の OpenAICompatibleAdapter) で api_key 付きのまま拾われる。
    conn = get_db()
    try:
        rows = {
            r["key"]: r["value"]
            for r in conn.execute(
                "SELECT key, value FROM settings "
                "WHERE key IN ('llm_endpoint', 'llm_provider', 'llm_model', 'llm_api_key_enc')"
            ).fetchall()
        }
    finally:
        conn.close()
    endpoint = rows.get("llm_endpoint") or default_llm_endpoint()
    provider = rows.get("llm_provider") or "lmstudio"
    model = rows.get("llm_model") or ""
    import vault_enc
    _enc = rows.get("llm_api_key_enc") or ""
    _api_key = vault_enc.dec_raw(_enc) if _enc else ""

    def _canon_base(value: str) -> str:
        base = (value or "").rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
        return base

    def _adapter_provider(adapter) -> str:
        if isinstance(adapter, MockAdapter):
            return "mock"
        if isinstance(adapter, OpenAICompatibleAdapter):
            return getattr(adapter, "provider", "") or "openai_compat"
        return "lmstudio"

    adapter = _state.adapter
    current_provider = _adapter_provider(adapter)
    current_base = _canon_base(getattr(adapter, "base_url", ""))
    desired_base = _canon_base(endpoint)
    current_model = getattr(adapter, "model", "") or ""
    desired_model = "" if (model or "").strip() == "auto" else (model or "")
    if (
        current_provider == provider
        and current_base == desired_base
        and current_model == desired_model
    ):
        return adapter

    adapter = get_llm_adapter(base_url=endpoint, mock=False, provider=provider, model=model, api_key=_api_key)
    _state.adapter = adapter
    return adapter


# ─────────────────────────────────────────────
# レポート/比較系で共有する LLM 呼び出しヘルパー
# (server.py から切り出し。routers/chat.py + routers/reports.py が利用)
# ─────────────────────────────────────────────

import logging as _logging

_logger = _logging.getLogger("cynovela")


def _resolve_active_llm() -> tuple[str, str]:
    """settings から現在の LLM endpoint + model を取得 (defaults LM Studio).

    bundled-config-20260731: 参照するキー名を実際に書かれている `llm_endpoint` /
    `llm_model` へ直した。従来はドット記法の `llm.base_url` / `llm.model` を読んで
    いたが、この2つを書く経路はコード中に存在せず (書き口は routers/llm.py の
    `llm_endpoint`)、必ず未設定として扱われていた。結果、既定値へ落ちるうえ、その
    既定値が `http://localhost:1234/v1` の固定値だったため、コンテナ形態では
    localhost が自コンテナを指して常に不達だった。
    既定値も `default_llm_endpoint()` に寄せ、形態 (直起動 / コンテナ) に応じて
    起動時に解決させる。値を焼き込まない。
    """
    conn = get_db()
    try:
        rows = {
            r["key"]: r["value"]
            for r in conn.execute(
                "SELECT key, value FROM settings " "WHERE key IN ('llm_endpoint', 'llm_model')"
            ).fetchall()
        }
    finally:
        conn.close()
    endpoint = rows.get("llm_endpoint") or default_llm_endpoint()
    model = rows.get("llm_model") or "auto"
    return endpoint, model


async def _call_llm_simple(prompt: str, max_tokens: int = 600) -> str:
    """単一 user prompt で LLM を呼ぶシンプルラッパー (レポート生成用).

    既存 rag.call_llm を流用 (--mock モード時は MockAdapter が動く)。
    失敗時は文字列で error を返す (例外を投げない)。
    """
    try:
        endpoint, model = _resolve_active_llm()
        from rag import call_llm as _call

        # P1: 現在有効な adapter (OpenRouter なら RAM 保持の api_key 付き) を流用。
        # adapter が無指定だと call_llm が keyless な LMStudio adapter を生成してしまうため。
        adapter = get_current_adapter()
        if isinstance(adapter, OpenAICompatibleAdapter):
            endpoint = getattr(adapter, "base_url", endpoint)
            model = getattr(adapter, "model", "") or model

        result = await _call(
            prompt,
            endpoint,
            model,
            temperature=0.2,
            params={"max_tokens": max_tokens},
            adapter=adapter,
        )
        # P0-1: rag.call_llm がタプル (answer, reasoning_content) を返す場合があるため第1要素のみ返す
        if isinstance(result, tuple):
            result = result[0]
        return result
    except Exception as e:
        _logger.exception(f"LLM call failed: {e}")
        return "(LLMへの接続でエラーが発生しました)"
