"""Cynovela — LLMプロバイダーアダプター。

LM Studio (OpenAI互換 /v1 API) への直接httpxコールを集約する。
max_tokensは意図的に省略する（Reasoningモデルのbudget消費対策）。
将来の Ollama / OpenRouter 切替もこの層で吸収する。
"""

from __future__ import annotations

import os
import json as _json
import httpx


def _normalize_base(base_url: str) -> str:
    """末尾の '/' と '/v1' を削り、ベースURLを整える。"""
    b = (base_url or "").rstrip("/")
    if b.endswith("/v1"):
        b = b[: -len("/v1")]
    return b


def _norm_model(model: str) -> str:
    """C-B2 20260729: "auto" は未指定と同義に扱う（rag.py call_llm と同じ語彙）。"""
    m = (model or "").strip()
    return "" if m in ("", "auto") else m


class ModelNotFoundError(RuntimeError):
    """DD-CYN-0094 C: 指定されたモデルが受け皿に無い。名前と理由を画面まで運ぶ。"""

    def __init__(self, model_id: str):
        self.model_id = model_id
        super().__init__(
            f"指定されたモデル『{model_id}』が見つかりません。"
            "設定で選び直すか、LLM 側で読み込んでください。"
        )


# DD-CYN-0094 C: 受け皿ごとのモデル不在応答の言い回し（LM Studio / Ollama / OpenRouter）。
_MODEL_NOT_FOUND_HINTS = (
    "not found",
    "not_found",
    "not a valid",
    "does not exist",
    "try pulling",
    "failed to load",
    "no models loaded",
)


def _maybe_raise_model_not_found(status_code: int, body_text: str, model_id: str) -> None:
    """chat/completions の 4xx/5xx 応答がモデル不在を示すなら ModelNotFoundError にする。"""
    if status_code >= 400 and model_id:
        b = (body_text or "").lower()
        if "model" in b and any(h in b for h in _MODEL_NOT_FOUND_HINTS):
            raise ModelNotFoundError(model_id)


def _raise_for_chat_status(r, model_id: str) -> None:
    """DD-CYN-0094 C: モデル不在は名前つきで区別し、それ以外は従来どおり HTTP エラー。"""
    if r.status_code >= 400:
        try:
            _body = r.text
        except Exception:
            _body = ""
        _maybe_raise_model_not_found(r.status_code, _body, model_id)
    r.raise_for_status()


# C-B2 20260729: /v1/models 一覧に埋め込み・再ランク専用モデルが混在するため、
# id 文字列で判別できるものを自動選択の候補から外す。
_NON_CHAT_MODEL_HINTS = ("embed", "embedding", "rerank", "reranker")


def _pick_chat_model(models: list[dict]) -> str:
    """一覧から埋め込み・再ランク専用でない先頭の id を返す。
    候補が残らない場合は従来どおり一覧の先頭を返す。"""
    if not models:
        return ""
    for m in models:
        mid = (m.get("id") or "")
        if mid and not any(h in mid.lower() for h in _NON_CHAT_MODEL_HINTS):
            return mid
    return models[0].get("id", "")


def _get_llm_base_url_from_config(default: str = "http://localhost:1234") -> str:
    """PORTABILITY FIX 20260527 P4: cynovela.yaml の llm.base_url を読む。
    yaml ロード失敗時は default を返す（後方互換）。"""
    try:
        from core.config import load_yaml_config
        return (load_yaml_config().get("llm") or {}).get("base_url") or default
    except Exception:
        return default


class LMStudioAdapter:
    def __init__(self, base_url: str = "", model: str = ""):
        self.base_url = _normalize_base(base_url or _get_llm_base_url_from_config())
        self.model = _norm_model(model)

    async def list_models(self) -> list[dict]:
        """LM Studioにロード済みモデル一覧を返す。失敗時は空。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/v1/models")
                r.raise_for_status()
                return r.json().get("data", []) or []
        except Exception:
            return []

    async def has_loaded_model(self) -> tuple[bool, str]:
        """(ロード済みか, チャット可能な先頭モデルID) を返す。"""
        models = await self.list_models()
        if models:
            return True, _pick_chat_model(models)
        return False, ""

    async def test_connection(self) -> dict:
        """接続テスト用の詳細情報。"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/v1/models")
                r.raise_for_status()
                models = r.json().get("data", []) or []
                return {
                    "status": "connected",
                    "models": len(models),
                    "endpoint": f"{self.base_url}/v1",
                }
        except Exception as e:
            return {
                "status": "disconnected",
                "error": str(e),
                "endpoint": f"{self.base_url}/v1",
            }

    async def chat(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
    ) -> str:
        """チャット補完。max_tokensは渡さない（Reasoningモデル対応）。"""
        model_id = _norm_model(model_id)  # C-B2 20260729: 引数の "auto" も未指定扱い
        if not model_id:
            model_id = _norm_model(self.model)  # DD-CYN-0094 B: 送る直前でも "auto" を倒す
        if not model_id:
            ok, mid = await self.has_loaded_model()
            if not ok:
                raise RuntimeError("LM Studioにモデルがロードされていません")
            model_id = mid
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            # max_tokens: 意図的に省略（Reasoningモデルでbudget枯渇を防ぐ）
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            )
            _raise_for_chat_status(r, model_id)  # DD-CYN-0094 C
            return r.json()["choices"][0]["message"]["content"]

    async def chat_with_usage(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
        params: dict | None = None,
    ) -> tuple[str, str, dict]:
        """#09 Step C: 応答テキストと usage / finish_reason を返す。
        #06: params (dict) で top_p / top_k / repeat_penalty / seed 等を渡せる。
              LM Studio では max_tokens は意図的に無視する (Reasoning モデル対応)。
        """
        model_id = _norm_model(model_id)  # C-B2 20260729: 引数の "auto" も未指定扱い
        if not model_id:
            model_id = _norm_model(self.model)  # DD-CYN-0094 B: 送る直前でも "auto" を倒す
        if not model_id:
            ok, mid = await self.has_loaded_model()
            if not ok:
                raise RuntimeError("LM Studioにモデルがロードされていません")
            model_id = mid
        payload = {
            "model": model_id,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        # #06: 任意パラメータの取り込み（max_tokens は除外）
        if params:
            for k in ("top_p", "top_k", "repeat_penalty", "seed"):
                if params.get(k) is not None:
                    payload[k] = params[k]
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            )
            _raise_for_chat_status(r, model_id)  # DD-CYN-0094 C
            data = r.json() or {}
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            reasoning_content = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "") or ""
            finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
            usage = data.get("usage") or {}
            return answer, reasoning_content, {**usage, "finish_reason": finish_reason}


class MockAdapter:
    """LM Studio不要のモックLLMアダプター。デモ・開発用途。

    Stage3の段階では、プロバイダー差し替えのレイヤー検証と
    「LM Studio未接続でもUIが一通り触れる」体験を提供するのが目的。
    """

    def __init__(self):
        self.base_url = "mock://localhost"

    async def list_models(self) -> list[dict]:
        return [{"id": "mock-model", "object": "model"}]

    async def has_loaded_model(self) -> tuple[bool, str]:
        return True, "mock-model"

    async def test_connection(self) -> dict:
        return {"status": "connected", "models": 1, "endpoint": "mock://localhost"}

    async def chat(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
    ) -> str:
        # systemメッセージの先頭100文字とlast userメッセージを抽出
        system_text = ""
        user_question = ""
        for m in messages or []:
            role = m.get("role")
            content = m.get("content", "") or ""
            if role == "system" and not system_text:
                system_text = content
            elif role == "user":
                user_question = content  # 最後のuserが残る
        context_preview = system_text[:100]
        return (
            f"[モック回答] 質問: 「{user_question}」\n"
            f"コンテキスト（先頭100文字）: {context_preview}…\n"
            f"（MockAdapterによる応答 — LM Studio不要）"
        )

    async def chat_with_usage(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
        params: dict | None = None,
    ) -> tuple[str, str, dict]:
        """#09 Step C: モック応答 — usage は空dict + finish_reason='stop'。
        #06: params は無視 (モックは固定応答)"""
        ans = await self.chat(messages, model_id=model_id, temperature=temperature)
        # 簡易的なトークン推定（文字数ベース）— モックでも UI 動作確認用に値を返す
        prompt_chars = sum(len((m or {}).get("content", "") or "") for m in (messages or []))
        completion_chars = len(ans or "")
        prompt_tokens = max(1, prompt_chars // 3)
        completion_tokens = max(1, completion_chars // 3)
        return ans, "", {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "finish_reason": "stop",
        }


class OpenAICompatibleAdapter:
    """OpenAI互換 /v1 API を持つ任意のサービス（OpenRouter / vLLM / Ollama 等）用の汎用Adapter。

    api_key は引数 (設定UIのフォーム入力) のみ。fix2-v4-A: このセッションのRAM上にのみ保持し、
    環境変数・DB・設定ファイルには保存も参照もしない。
    max_tokens は意図的に省略（既存ポリシー）
    """

    def __init__(self, base_url: str, model: str = "", api_key: str = ""):
        self.base_url = _normalize_base(base_url)
        self.model = _norm_model(model)
        self.api_key = api_key or ""
        self.provider = ""  # F4: "ollama" / "lmstudio" 等

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_models(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                r.raise_for_status()
                return r.json().get("data", []) or []
        except Exception:
            return []

    @property
    def is_ollama(self) -> bool:
        """F1: URL またはプロバイダー種別で Ollama を判定する。"""
        return self.provider == "ollama" or ":11434" in self.base_url or "ollama" in self.base_url.lower()

    async def fetch_context_length(self, model_id: str = "") -> int:
        """F2: Ollama /api/show でコンテキスト長を取得する。非 Ollama は 4096 を返す。"""
        mid = _norm_model(model_id) or self.model  # C-B2 20260729: 引数の "auto" も未指定扱い
        if not self.is_ollama or not mid:
            return 4096
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{self.base_url}/api/show", json={"model": mid})
                r.raise_for_status()
                info = r.json().get("model_info") or {}
                ctx = info.get("llama.context_length") or info.get("context_length")
                return int(ctx) if ctx else 4096
        except Exception:
            return 4096

    async def ensure_model_loaded(self, model_id: str = "") -> bool:
        """F3: Ollama でモデルの存在を確認する（推論時自動ロード）。"""
        mid = _norm_model(model_id) or self.model  # C-B2 20260729: 引数の "auto" も未指定扱い
        if not self.is_ollama:
            return True
        if not mid:
            ok, _ = await self.has_loaded_model()
            return ok
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(f"{self.base_url}/api/show", json={"model": mid})
                return r.status_code == 200
        except Exception:
            return False

    async def has_loaded_model(self) -> tuple[bool, str]:
        if self.model:
            return True, self.model
        models = await self.list_models()
        if models:
            return True, _pick_chat_model(models)
        return False, ""

    async def test_connection(self) -> dict:
        # fix-bug2: localhost/127.0.0.1 はキー不要のローカル LLM (Ollama 等) が多い。
        # キー未設定でもローカルなら接続を試みる。リモートのみ warning で早期返却する。
        _local = ("localhost" in self.base_url) or ("127.0.0.1" in self.base_url)
        # keywarning-20260630: 鍵警告は鍵が要るプロバイダー (openai_compat/OpenRouter) のみ。
        #   ollama/vllm 等はリモート (host.containers.internal 等) でも鍵不要なので警告を出さない。
        _key_required = self.provider in ("openai_compat", "openrouter")
        if not self.api_key and not _local and _key_required:
            return {
                "status": "warning",
                "models": 0,
                "endpoint": f"{self.base_url}/v1",
                "error": "API key 未設定（設定UI から。このセッションのみ保持し保存しません）",
            }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/v1/models", headers=self._headers())
                r.raise_for_status()
                models = r.json().get("data", []) or []
                return {
                    "status": "connected",
                    "models": len(models),
                    "endpoint": f"{self.base_url}/v1",
                    "current_model": self.model or (models[0].get("id", "") if models else ""),
                }
        except Exception as e:
            return {
                "status": "disconnected",
                "error": str(e),
                "endpoint": f"{self.base_url}/v1",
            }

    async def chat(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
    ) -> str:
        mid = _norm_model(model_id) or _norm_model(self.model)  # DD-CYN-0094 B: 送る直前でも "auto" を倒す
        if not mid:
            ok, mid = await self.has_loaded_model()
            if not ok:
                raise RuntimeError("OpenAI互換: モデルが指定されていません")
        payload = {
            "model": mid,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            # max_tokens: 意図的に省略
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            _raise_for_chat_status(r, mid)  # DD-CYN-0094 C
            return r.json()["choices"][0]["message"]["content"]

    async def chat_with_usage(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
        params: dict | None = None,
    ) -> tuple[str, str, dict]:
        """#09 Step C: OpenAI互換 — usage / finish_reason を返す。
        #06: params で top_p / top_k / max_tokens / repeat_penalty / seed を渡せる。"""
        mid = _norm_model(model_id) or _norm_model(self.model)  # DD-CYN-0094 B: 送る直前でも "auto" を倒す
        if not mid:
            ok, mid = await self.has_loaded_model()
            if not ok:
                raise RuntimeError("OpenAI互換: モデルが指定されていません")
        payload = {
            "model": mid,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        # #06: OpenAI互換は max_tokens も含めて任意パラメータを取り込む
        if params:
            for k in ("top_p", "top_k", "max_tokens", "repeat_penalty", "seed"):
                if params.get(k) is not None:
                    payload[k] = params[k]
        # F5: Ollama の think パラメータ（enable_thinking 対応）
        if self.is_ollama and params:
            think_val = params.get("enable_thinking") if params.get("enable_thinking") is not None else params.get("think")
            if think_val is not None:
                payload["think"] = bool(think_val)
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            _raise_for_chat_status(r, mid)  # DD-CYN-0094 C
            data = r.json() or {}
            answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            reasoning_content = data.get("choices", [{}])[0].get("message", {}).get("reasoning_content", "") or ""
            finish_reason = data.get("choices", [{}])[0].get("finish_reason", "")
            usage = data.get("usage") or {}
            return answer, reasoning_content, {**usage, "finish_reason": finish_reason}

    async def chat_stream(
        self,
        messages: list[dict],
        model_id: str = "",
        temperature: float = 0.1,
    ):
        """SSEストリーミング応答をAsyncGeneratorとして返す（将来UI用）。"""
        mid = _norm_model(model_id) or _norm_model(self.model)  # DD-CYN-0094 B: 送る直前でも "auto" を倒す
        if not mid:
            ok, mid = await self.has_loaded_model()
            if not ok:
                raise RuntimeError("OpenAI互換: モデルが指定されていません")
        payload = {
            "model": mid,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as r:
                if r.status_code >= 400:  # DD-CYN-0094 C
                    _sbody = (await r.aread()).decode("utf-8", "replace")
                    _maybe_raise_model_not_found(r.status_code, _sbody, mid)
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[len("data:") :].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        ev = _json.loads(chunk)
                    except Exception:
                        continue
                    delta = (ev.get("choices") or [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta


def get_llm_adapter(
    base_url: str = "",
    mock: bool = False,
    provider: str = "lmstudio",
    model: str = "",
    api_key: str = "",
):
    # PORTABILITY FIX 20260527 P4: 空文字なら cynovela.yaml の llm.base_url から解決
    if not base_url:
        base_url = _get_llm_base_url_from_config()
    """LLMアダプターを返す。

    優先順位:
      1) mock=True → MockAdapter（後方互換）
      2) provider="mock" → MockAdapter
      3) provider in {openai_compat, ollama, openrouter, vllm} → OpenAICompatibleAdapter
      4) その他（既定 lmstudio）→ LMStudioAdapter
    """
    if mock or provider == "mock":
        return MockAdapter()
    # F1: Ollama 等の OpenAI 互換プロバイダーを OpenAICompatibleAdapter にマップする。
    #     これにより model 名がリクエストに乗り、かつ get_current_adapter() の
    #     isinstance ショートサーキットで切替後アダプターが維持される。
    if provider in ("openai_compat", "ollama", "openrouter", "vllm"):
        _a = OpenAICompatibleAdapter(base_url=base_url, model=model, api_key=api_key)
        _a.provider = provider  # F4: プロバイダー種別を保持
        return _a
    return LMStudioAdapter(base_url, model=model)
