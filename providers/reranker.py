"""Cynovela — RerankerProvider 抽象層。

LM Studio は /v1/rerank を持たないため Reranker は LLMProvider と独立した層として設計。
ローカル: NoReranker / CrossEncoder / MLX (skel) / Ollama
クラウド: Cohere / Jina / Voyage / OpenAI互換 (任意の /v1/rerank)
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass
import httpx


@dataclass
class RerankResult:
    chunk_id: str
    score: float
    original_rank: int
    reranked_rank: int


class RerankerProvider:
    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_n: int = 5,
    ) -> list[RerankResult]:
        raise NotImplementedError

    async def test_connection(self) -> dict:
        raise NotImplementedError


class NoReranker(RerankerProvider):
    """Rerank無し。chunks のスコア順をそのまま返す。"""

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        out = []
        for i, c in enumerate(chunks[:top_n]):
            out.append(
                RerankResult(
                    chunk_id=c.get("chunk_id", ""),
                    score=float(c.get("score", c.get("hybrid_score", 0.0))),
                    original_rank=i,
                    reranked_rank=i,
                )
            )
        return out

    async def test_connection(self) -> dict:
        return {"status": "ok", "provider": "none"}

    async def is_available(self) -> bool:
        return True


def _text_of(chunk: dict) -> str:
    return chunk.get("content") or chunk.get("content_preview") or chunk.get("text") or ""


def _chunk_id(chunk: dict) -> str:
    return chunk.get("chunk_id") or chunk.get("id") or ""


class CrossEncoderReranker(RerankerProvider):
    """sentence-transformers の CrossEncoder を使用するローカル Reranker。"""

    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3"):
        # 既定は多言語対応 (BGE Reranker v2 m3)。日本語クエリでも精度を維持する。
        self.model_name = model
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            try:
                from core.config import CYNOVELA_CONFIG

                _max_len = int((CYNOVELA_CONFIG.get("rag") or {}).get("reranker_max_length", 512))
            except Exception:
                _max_len = 512
            # Phase 0c: モデルパス自動解決 (cynovela.yaml.models.reranker.path > 配布同梱 > HFキャッシュ)
            try:
                from core.model_paths import resolve_model_path, get_configured_model

                _name, _path = get_configured_model("reranker")
                resolved = resolve_model_path(_name or self.model_name, _path)
            except Exception:
                resolved = self.model_name
            # resolved がローカルディレクトリなら直接ロード（local_files_only 不要）。
            # それ以外（HF model ID）なら local_files_only=True で意図しないネットワーク
            # 取得を防ぐ（事前に preflight でダウンロード済みである前提）。
            # embedding.py の LocalSentenceTransformerProvider._ensure_model と同じパターン。
            import os as _os
            import pathlib as _pl

            # --- PORTABILITY FIX: TAR 配布同梱 store/models/ をローカルパス候補に追加 ---
            # resolve_model_path() で見つからなかった場合 (resolved がモデルID文字列のまま)、
            # {repo_root}/store/models/ 配下を 2 形式で探索する。
            #   形式1: store/models/{model_name}                    （直接形式 / 簡易配布用）
            #   形式2: store/models/models--{org}--{name}/snapshots/{hash}  （HFキャッシュ形式）
            # TAR パッケージ展開直後で ~/.cynovela/models/ が空の新環境でも動くようにする目的。
            try:
                if not _pl.Path(str(resolved)).is_dir():
                    _app_dir = _pl.Path(__file__).resolve().parent.parent
                    _store_models = _app_dir / "store" / "models"
                    _direct = _store_models / self.model_name.replace("/", _os.sep)
                    _hf_dir = _store_models / ("models--" + self.model_name.replace("/", "--"))
                    if _direct.is_dir():
                        resolved = str(_direct)
                    elif _hf_dir.is_dir():
                        _snapshots = sorted((_hf_dir / "snapshots").glob("*"))
                        if _snapshots:
                            resolved = str(_snapshots[-1])
            except Exception:
                pass
            # --- END PORTABILITY FIX ---

            # 状態は store/ 配下に集約 (ホームに状態を置かない)。DLフォールバック先も store/models。
            _models_base = _os.environ.get("CYNOVELA_DATA_DIR") or _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "store"
            )
            cache_dir = _os.path.join(_models_base, "models")
            _os.makedirs(cache_dir, exist_ok=True)
            _is_local_path = _pl.Path(str(resolved)).is_dir()
            # max_length 指定で BGE-Reranker-v2-m3 (512) の無警告切り捨てを抑止
            if _is_local_path:
                self._model = CrossEncoder(str(resolved), max_length=_max_len)
            else:
                self._model = CrossEncoder(
                    str(resolved),
                    max_length=_max_len,
                    cache_folder=cache_dir,
                    local_files_only=True,
                )
        return self._model

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        if not chunks:
            return []
        m = self._ensure_model()
        pairs = [(query, _text_of(c)) for c in chunks]
        scores = await asyncio.to_thread(m.predict, pairs)
        ranked = sorted(
            [(i, float(scores[i])) for i in range(len(chunks))],
            key=lambda x: x[1],
            reverse=True,
        )[:top_n]
        return [
            RerankResult(
                chunk_id=_chunk_id(chunks[i]),
                score=s,
                original_rank=i,
                reranked_rank=r,
            )
            for r, (i, s) in enumerate(ranked)
        ]

    async def test_connection(self) -> dict:
        try:
            self._ensure_model()
            return {"status": "connected", "provider": "cross_encoder", "model": self.model_name}
        except Exception as e:
            return {"status": "error", "provider": "cross_encoder", "error": str(e)}


class FlashRankReranker(RerankerProvider):
    """PHASE A-8: FlashRank — 軽量 Reranker (初回 ~75MB のモデルダウンロード)。

    sentence-transformers の CrossEncoder より軽量で起動が速い。
    FlashRank ライブラリ自体に複数モデルが同梱されており、デフォルトは
    ms-marco-MiniLM 系の小型モデル。
    """

    def __init__(self, model: str = "ms-marco-MiniLM-L-12-v2", cache_dir: str | None = None):
        self.model_name = model
        self.cache_dir = cache_dir
        self._ranker = None

    def _ensure_ranker(self):
        if self._ranker is None:
            from flashrank import Ranker  # 遅延インポート

            kwargs = {"model_name": self.model_name}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._ranker = Ranker(**kwargs)
        return self._ranker

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        if not chunks:
            return []
        ranker = self._ensure_ranker()
        from flashrank import RerankRequest  # 遅延インポート

        passages = [{"id": str(i), "text": _text_of(c), "meta": {}} for i, c in enumerate(chunks)]
        req = RerankRequest(query=query, passages=passages)
        ranked_raw = await asyncio.to_thread(ranker.rerank, req)
        # FlashRank は score 降順のリストを返す
        out: list[RerankResult] = []
        for r, item in enumerate(ranked_raw[:top_n]):
            try:
                orig_i = int(item.get("id"))
            except (TypeError, ValueError):
                orig_i = r
            score = float(item.get("score", 0.0))
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(chunks[orig_i]) if 0 <= orig_i < len(chunks) else "",
                    score=score,
                    original_rank=orig_i,
                    reranked_rank=r,
                )
            )
        return out

    async def test_connection(self) -> dict:
        try:
            self._ensure_ranker()
            return {"status": "connected", "provider": "flashrank", "model": self.model_name}
        except ImportError:
            return {
                "status": "error",
                "provider": "flashrank",
                "error": "flashrank が未インストール (pip install flashrank)",
            }
        except Exception as e:
            return {"status": "error", "provider": "flashrank", "error": str(e)}


class MLXReranker(RerankerProvider):
    """MLX 用 Reranker（骨格のみ）。"""

    def __init__(self, model: str = "mlx-community/bge-reranker-v2-m3-4bit"):
        self.model_name = model

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        raise NotImplementedError("MLX Reranker は将来実装予定")

    async def test_connection(self) -> dict:
        return {"status": "not_implemented", "provider": "mlx", "model": self.model_name}


class OllamaReranker(RerankerProvider):
    """Ollama 0.3+ の /api/rerank を使用する Reranker。"""

    def __init__(self, base_url: str = "http://localhost:11434", model: str = ""):
        self.base_url = (base_url or "http://localhost:11434").rstrip("/")
        self.model = model

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        documents = [_text_of(c) for c in chunks]
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{self.base_url}/api/rerank",
                json={"model": self.model, "query": query, "documents": documents, "top_n": top_n},
            )
            r.raise_for_status()
            data = r.json()
        # Ollama: results = [{"index": int, "relevance_score": float}, ...]
        results = data.get("results") or data.get("data") or []
        out = []
        for r_idx, item in enumerate(results[:top_n]):
            i = int(item.get("index", r_idx))
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(chunks[i]) if 0 <= i < len(chunks) else "",
                    score=float(item.get("relevance_score") or item.get("score") or 0.0),
                    original_rank=i,
                    reranked_rank=r_idx,
                )
            )
        return out

    async def test_connection(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                if r.status_code < 500:
                    return {"status": "connected", "provider": "ollama", "url": self.base_url}
                return {"status": "error", "provider": "ollama", "code": r.status_code}
        except Exception as e:
            return {"status": "disconnected", "provider": "ollama", "error": str(e)}


class _CloudRerankerBase(RerankerProvider):
    """CohereやVoyage系のレスポンス形式の共通実装。"""

    api_url: str = ""
    name: str = "cloud"

    def __init__(self, model: str = "", api_key: str = ""):
        self.model = model
        self.api_key = api_key or os.environ.get("CYNOVELA_RERANKER_API_KEY", "") or ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        documents = [_text_of(c) for c in chunks]
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                self.api_url,
                headers=self._headers(),
                json={"model": self.model, "query": query, "documents": documents, "top_n": top_n},
            )
            r.raise_for_status()
            data = r.json()
        results = data.get("results") or data.get("data") or []
        out = []
        for r_idx, item in enumerate(results[:top_n]):
            i = int(item.get("index", r_idx))
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(chunks[i]) if 0 <= i < len(chunks) else "",
                    score=float(item.get("relevance_score") or item.get("score") or 0.0),
                    original_rank=i,
                    reranked_rank=r_idx,
                )
            )
        return out

    async def test_connection(self) -> dict:
        if not self.api_key:
            return {"status": "warning", "provider": self.name, "error": "API key 未設定"}
        return {"status": "configured", "provider": self.name, "endpoint": self.api_url}


class CohereReranker(_CloudRerankerBase):
    api_url = "https://api.cohere.ai/v1/rerank"
    name = "cohere"

    def __init__(self, model: str = "rerank-multilingual-v3.0", api_key: str = ""):
        super().__init__(model=model, api_key=api_key)


class JinaReranker(_CloudRerankerBase):
    api_url = "https://api.jina.ai/v1/rerank"
    name = "jina"

    def __init__(self, model: str = "jina-reranker-v2-base-multilingual", api_key: str = ""):
        super().__init__(model=model, api_key=api_key)


class VoyageReranker(_CloudRerankerBase):
    api_url = "https://api.voyageai.com/v1/rerank"
    name = "voyage"

    def __init__(self, model: str = "rerank-2", api_key: str = ""):
        super().__init__(model=model, api_key=api_key)


class OpenAICompatibleReranker(_CloudRerankerBase):
    """{base_url}/v1/rerank を持つ任意のOpenAI互換 Reranker。"""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str = "", api_key: str = ""):
        b = (base_url or "").rstrip("/")
        if b.endswith("/v1"):
            b = b[: -len("/v1")]
        self.api_url = f"{b}/v1/rerank"
        super().__init__(model=model, api_key=api_key)


# ga-finish-20260727: 外の口 (Mac Accelerator Service) に届かないときの明示退避の状態。
# 埋め込みの _EMBED_FALLBACK_STATE (rag.py) と同型。黙って挙動が変わらないよう、
# 退避の発生を記録し /api/settings/reranker 経由で画面 (設定 > Reranker) へ出す。
_RERANK_FALLBACK_STATE = {"active": False, "since": None, "error": "", "target": ""}


def get_rerank_fallback_state() -> dict:
    """外の口からローカルへの退避状態のスナップショットを返す (UI 表示用)。"""
    return dict(_RERANK_FALLBACK_STATE)


class ExternalAcceleratorReranker(RerankerProvider):
    """ga-finish-20260727: 外の口 (Mac Accelerator Service) の /v1/rerank を使う Reranker。

    埋め込みの外出し (OpenAICompatibleEmbeddingProvider + content_class) と同じ作り:
      - 口へ渡す本文が伏字済みか原文かを常に明示する (content_class)
      - 口が居ないときは黙って待たせず明示的に退避する:
          * 再ランクの重みがローカル (store/models 等) にある → 本体の中で再ランク
            (in-process CrossEncoder。全部入り版は解凍してそのまま動く)
          * 重みが無い (軽量版) → 再ランクせず検索結果をそのまま返す (落ちない)
        どちらの経路に入ったかはログ1行 + _RERANK_FALLBACK_STATE (画面表示) に残す。
      - 復帰は次回の外部呼び出し成功時 (毎回まず外の口を試す)
    """

    def __init__(self, base_url: str, model: str = "", api_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[: -len("/v1")]
        self.model = model
        self.model_name = model
        self.api_key = api_key or os.environ.get("CYNOVELA_RERANKER_API_KEY", "") or ""
        # in-process 退避用 (遅延生成・一度だけ判定/ロード)
        self._local = None            # CrossEncoderReranker | None
        self._local_unavailable = False  # True = 重み無しを確認済み (毎回のロード試行を避ける)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _ensure_local(self):
        """in-process 退避先 (CrossEncoder) を返す。重みが無ければ None。"""
        if self._local is not None:
            return self._local
        if self._local_unavailable:
            return None
        try:
            _ce = CrossEncoderReranker(model=self.model or "BAAI/bge-reranker-v2-m3")
            _ce._ensure_model()  # 重みが無ければここで例外 (local_files_only)
            self._local = _ce
            return self._local
        except Exception:
            self._local_unavailable = True
            return None

    async def rerank(
        self,
        query: str,
        chunks: list[dict],
        top_n: int = 5,
        content_class: str = "masked",
    ) -> list[RerankResult]:
        if not chunks:
            return []
        documents = [_text_of(c) for c in chunks]
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=3.0)) as client:
                r = await client.post(
                    f"{self.base_url}/v1/rerank",
                    headers=self._headers(),
                    json={
                        "model": self.model,
                        "query": query,
                        "documents": documents,
                        "top_n": top_n,
                        "content_class": content_class or "masked",
                    },
                )
                r.raise_for_status()
                data = r.json()
        except Exception as _ex:
            return await self._rerank_fallback(query, chunks, top_n, _ex)
        if _RERANK_FALLBACK_STATE.get("active"):
            _RERANK_FALLBACK_STATE.update(active=False, error="", target="")
            print("[Cynovela] 外の口 (rerank) への接続が復帰しました (退避解除)")
        results = data.get("results") or data.get("data") or []
        out = []
        for r_idx, item in enumerate(results[:top_n]):
            i = int(item.get("index", r_idx))
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(chunks[i]) if 0 <= i < len(chunks) else "",
                    score=float(item.get("relevance_score") or item.get("score") or 0.0),
                    original_rank=i,
                    reranked_rank=r_idx,
                )
            )
        return out

    async def _rerank_fallback(self, query, chunks, top_n, ex) -> list[RerankResult]:
        """口が居ないときの退避。重みあり=本体内で再ランク / 重み無し=素通し。"""
        from datetime import datetime as _dt

        _local = self._ensure_local()
        _target = "in-process (cross_encoder)" if _local is not None else "none (再ランクなし・素通し)"
        _was_active = _RERANK_FALLBACK_STATE.get("active")
        _RERANK_FALLBACK_STATE.update(
            active=True,
            since=_RERANK_FALLBACK_STATE.get("since") if _was_active else _dt.now().isoformat(timespec="seconds"),
            error=str(ex),
            target=_target,
        )
        # 要件: どちらの経路に入ったかをログ1行に残す (無言禁止)
        print(
            f"[Cynovela] 外の口 (rerank {self.base_url}) に届かないため退避します: "
            f"経路={_target}: {ex}"
        )
        if _local is not None:
            return await _local.rerank(query, chunks, top_n=top_n)
        # 重み無し: 検索結果をそのまま返す (NoReranker と同じ形・落ちない)
        out = []
        for i, c in enumerate(chunks[:top_n]):
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(c),
                    score=float(c.get("score", c.get("hybrid_score", 0.0)) or 0.0),
                    original_rank=i,
                    reranked_rank=i,
                )
            )
        return out

    async def test_connection(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base_url}/health", headers=self._headers())
                if r.status_code == 200:
                    return {
                        "status": "connected",
                        "provider": "external_accelerator",
                        "endpoint": self.base_url,
                        "detail": r.json(),
                    }
                return {"status": "error", "provider": "external_accelerator", "code": r.status_code}
        except Exception as e:
            _local_ok = self._ensure_local() is not None
            return {
                "status": "disconnected",
                "provider": "external_accelerator",
                "endpoint": self.base_url,
                "error": str(e),
                "fallback": "in_process" if _local_ok else "none",
            }


class HttpReranker(RerankerProvider):
    """BLOCK B-4: TEI (Text Embeddings Inference) 互換 HTTP Reranker。
    x86 CPU でも動作 (GPU不要)。エンドポイント: {endpoint}/rerank
    レスポンス: [{"index": int, "score": float}, ...]
    """

    def __init__(self, endpoint: str, model: str = "", api_key: str = ""):
        self.endpoint = (endpoint or "").rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("CYNOVELA_RERANKER_API_KEY", "") or ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def rerank(self, query: str, chunks: list[dict], top_n: int = 5) -> list[RerankResult]:
        if not chunks:
            return []
        texts = [_text_of(c) for c in chunks]
        payload = {"query": query, "texts": texts, "truncate": True}
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{self.endpoint}/rerank",
                headers=self._headers(),
                json=payload,
            )
            r.raise_for_status()
            results = r.json()
        # TEI は list[{index, score}] を返す
        if not isinstance(results, list):
            results = results.get("results") or results.get("data") or []
        sorted_res = sorted(results, key=lambda x: float(x.get("score", 0.0)), reverse=True)[:top_n]
        out = []
        for r_idx, item in enumerate(sorted_res):
            i = int(item.get("index", r_idx))
            out.append(
                RerankResult(
                    chunk_id=_chunk_id(chunks[i]) if 0 <= i < len(chunks) else "",
                    score=float(item.get("score", 0.0)),
                    original_rank=i,
                    reranked_rank=r_idx,
                )
            )
        return out

    async def test_connection(self) -> dict:
        if not self.endpoint:
            return {"status": "error", "provider": "http_tei", "error": "endpoint 未設定"}
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.endpoint}/health", headers=self._headers())
                if r.status_code == 200:
                    return {"status": "ok", "provider": "http_tei", "endpoint": self.endpoint}
                return {"status": "error", "provider": "http_tei", "endpoint": self.endpoint, "code": r.status_code}
        except Exception as e:
            return {"status": "disconnected", "provider": "http_tei", "endpoint": self.endpoint, "error": str(e)}

    async def is_available(self) -> bool:
        """P2-4: TEI エンドポイントの生存確認 (短い timeout で /health へ HEAD or GET)。"""
        if not self.endpoint:
            return False
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.endpoint}/health", headers=self._headers())
                return r.status_code == 200
        except Exception:
            return False


def get_reranker_provider(config: dict) -> RerankerProvider:
    r = (config or {}).get("reranker", {}) or {}
    provider = (r.get("provider") or "none").lower()
    model = r.get("model") or ""
    base_url = r.get("base_url") or ""
    api_key = r.get("api_key") or ""
    # ga-finish-20260727: 外の口 (Mac Accelerator Service) への切り替え。
    # 埋め込み (providers/embedding.py get_embedding_provider) と同じ仕組み:
    #   device: external / external_accelerator + base_url で外の口を指す。
    #   provider=external_accelerator も同義として受ける (設定画面の一覧から選ぶ経路)。
    device = (r.get("device") or "").lower()
    if device in ("external", "external_accelerator") or provider == "external_accelerator":
        return ExternalAcceleratorReranker(base_url=base_url, model=model, api_key=api_key)
    if provider == "cross_encoder":
        return CrossEncoderReranker(model=model or "cross-encoder/ms-marco-MiniLM-L-6-v2")
    if provider == "flashrank":
        return FlashRankReranker(model=model or "ms-marco-MiniLM-L-12-v2")
    if provider == "mlx":
        return MLXReranker(model=model or "mlx-community/bge-reranker-v2-m3-4bit")
    if provider == "ollama":
        return OllamaReranker(base_url=base_url or "http://localhost:11434", model=model)
    if provider == "cohere":
        return CohereReranker(model=model or "rerank-multilingual-v3.0", api_key=api_key)
    if provider == "jina":
        return JinaReranker(model=model or "jina-reranker-v2-base-multilingual", api_key=api_key)
    if provider == "voyage":
        return VoyageReranker(model=model or "rerank-2", api_key=api_key)
    if provider == "openai_compat":
        return OpenAICompatibleReranker(base_url=base_url, model=model, api_key=api_key)
    if provider in ("http", "tei", "http_tei"):
        # BLOCK B-4: TEI互換 HTTP Reranker
        return HttpReranker(endpoint=base_url, model=model, api_key=api_key)
    return NoReranker()
