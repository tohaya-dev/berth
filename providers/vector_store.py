"""Cynovela — VectorStoreProvider 抽象層。

ChromaDB は現状の rag.py が直接呼んでいる。本Providerでラップして
将来 Qdrant 等に切り替えられるインターフェイスを提供する。

BLOCK A-4: 指示書互換の collection 単位 sync VectorStore も追加 (下部参照)。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
import logging
import os
import httpx

_log = logging.getLogger(__name__)


# masking-rework-overnight-v5 §段1c: Chroma collection 階層 (raw / masked) 命名
# 生本文は __raw collection、マスク済本文は __masked collection に格納する。
# 設計判断 (pageId 36694ef8-ac04-811c-9194-ce7d73a85098 「設計の正本」):
# 表示時のロール書き分けに頼らず、保管庫を構造的に分離して権限階層を扱う。
#
# masked-only (vector-tier-masked-only-20260724 §9-1 / DD-CYN-0115 M-7):
#   伏字前の層 ({cid}__raw) はもう「作らない」。定数・命名・読み出し/削除の口は
#   既存データ (過去に作られた __raw) の読み出し・掃除のために残す (後方互換)。
#   DEFAULT_TIER も 'raw' のまま変えない: 呼び手は全て tier を明示しており
#   (rag.py / server.py / routers)、既定値を動かすと読み出し側の意味まで変わるため。
#   代わりに「書き込みの口」だけを raw_tier_write_blocked() で塞ぐ。
TIER_RAW = "raw"
TIER_MASKED = "masked"
DEFAULT_TIER = TIER_RAW


def chroma_name_for_tier(collection_id: str, tier: str = DEFAULT_TIER) -> str:
    """collection_id を tier 付き Chroma collection 名に変換する。

    すでに '__raw' / '__masked' が付いている場合はそのまま返す (idempotent)。
    None / 空文字はそのまま透過 (Chroma 側で適切にエラーになる)。
    """
    if not collection_id:
        return collection_id
    if collection_id.endswith("__" + TIER_RAW) or collection_id.endswith("__" + TIER_MASKED):
        return collection_id
    _t = tier if tier in (TIER_RAW, TIER_MASKED) else DEFAULT_TIER
    return f"{collection_id}__{_t}"


def raw_tier_write_blocked(where: str, collection_id: str, tier: str = DEFAULT_TIER, n: int = 0) -> bool:
    """masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層への書き込みを塞ぐ共通判定。

    書き込み先の実名 (chroma_name_for_tier の結果) が '__raw' で終わるなら True を返し、
    呼び手はその書き込みを行わない (= {cid}__raw は新規に作られない・行が増えない)。
    読み出し (query/export/count) と削除 (delete_ids/delete_collection) は塞がない。
    過去に作られた __raw の中身は今までどおり読めて消せる (既存データ互換)。

    理由 (決着済み・事実101-1): ベクトルは距離計算を壊すため暗号化できず、伏字前由来の
    ベクターを置くことは暗号化されていない原文の写しを置くのとほぼ同義になる。

    例外は投げない (フェイルクローズを「書かない」側へ倒す)。取りこぼしに気づけるよう
    WARNING を残す。ChromaDB / pgvector の両実装がこの1つの判定を共有する。
    """
    name = chroma_name_for_tier(collection_id, tier)
    if not name or not name.endswith("__" + TIER_RAW):
        return False
    _log.warning(
        "[Cynovela] masked-only: 伏字前の層への書き込みを行いません "
        "(%s / collection=%s / tier=%s / 件数=%s)",
        where,
        name,
        tier,
        n,
    )
    return True


class VectorStoreProvider:
    async def add(self, collection_id: str, chunks: list[dict]) -> None:
        raise NotImplementedError

    async def search(self, collection_id: str, query_embedding: list[float], n: int) -> list[dict]:
        raise NotImplementedError

    async def delete_collection(self, collection_id: str) -> None:
        raise NotImplementedError

    async def export(self, collection_id: str) -> dict:
        raise NotImplementedError

    async def import_data(self, collection_id: str, data: dict) -> None:
        raise NotImplementedError

    async def test_connection(self) -> dict:
        raise NotImplementedError


class ChromaDBVectorStore(VectorStoreProvider):
    """既存の ChromaDB クライアントをラップする Provider。

    rag.py の get_chroma() / publish_collection_iter() 内のロジックを
    こちらにも委譲できるよう、最小限のCRUDを公開する。
    """

    def __init__(self, path: str = ""):
        # alpha §9-A-3: パッケージ配下 db/chroma。env CYNOVELA_CHROMA で上書き可。
        _vs_app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = path or os.path.expanduser(os.environ.get("CYNOVELA_CHROMA", os.path.join(_vs_app_dir, "db", "chroma")))
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            import chromadb

            self._client = chromadb.PersistentClient(path=self.path)
        return self._client

    async def add(self, collection_id: str, chunks: list[dict], tier: str = DEFAULT_TIER) -> None:
        """chunks: [{id, document, metadata}, ...] (embedding は ChromaDB 既定で生成)。

        §段1c: tier ('raw' / 'masked') で書き込み先 Chroma collection を分岐する。
        masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        """
        if not chunks:
            return
        if raw_tier_write_blocked("ChromaDBVectorStore.add", collection_id, tier, len(chunks)):
            return
        client = self._ensure_client()
        col = client.get_or_create_collection(name=chroma_name_for_tier(collection_id, tier))
        col.upsert(
            documents=[c.get("document", "") for c in chunks],
            ids=[c.get("id", "") for c in chunks],
            metadatas=[c.get("metadata", {}) for c in chunks],
        )

    async def search(
        self,
        collection_id: str,
        query_embedding: list[float],
        n: int,
        workspace_id: str | None = None,
        tier: str = DEFAULT_TIER,
    ) -> list[dict]:
        """Vector search. Stage R8-3: workspace_id を渡すと where 句で多重防御 (Agent N §3-1)。

        §段1c: tier ('raw' / 'masked') で読み出し先 Chroma collection を分岐。
        既定 tier='raw' (後方互換)。階層振り分けは §段2 で入口側が tier を選ぶ。
        """
        client = self._ensure_client()
        try:
            col = client.get_collection(name=chroma_name_for_tier(collection_id, tier))
            # Stage R8-3: workspace_id があれば where 句で絞り込み
            _kw_extra = {"where": {"workspace_id": workspace_id}} if workspace_id else {}
            res = col.query(query_embeddings=[query_embedding], n_results=n, **_kw_extra)
            ids = (res.get("ids") or [[]])[0]
            docs = (res.get("documents") or [[]])[0]
            metas = (res.get("metadatas") or [[]])[0]
            dists = (res.get("distances") or [[]])[0]
            return [
                {"id": i, "document": d, "metadata": m, "distance": dist}
                for i, d, m, dist in zip(ids, docs, metas, dists)
            ]
        except Exception:
            return []

    async def delete_collection(self, collection_id: str, tier: str | None = None) -> None:
        """§段1c: tier 未指定なら raw / masked 両方を削除 (collection 全体削除の意図)。"""
        client = self._ensure_client()
        targets = (
            [chroma_name_for_tier(collection_id, tier)]
            if tier in (TIER_RAW, TIER_MASKED)
            else [chroma_name_for_tier(collection_id, TIER_RAW), chroma_name_for_tier(collection_id, TIER_MASKED)]
        )
        for _name in targets:
            try:
                client.delete_collection(name=_name)
            except Exception:
                pass

    def delete_ids(self, collection_id: str, ids: list[str], tier: str = DEFAULT_TIER) -> None:
        """FIX-055: chunk 単位削除を抽象経由で公開 (rag.py の col.delete(ids=...) 置換用)。

        ChromaDB collection への直接アクセスを抽象内に閉じる。エラーは握り潰さず
        呼出側に伝播 (zombie 検出のため、FIX-044 rollback 経路で重要)。
        §段1c: tier で読み書き先を分岐 (既定 'raw')。
        """
        if not ids:
            return
        client = self._ensure_client()
        col = client.get_collection(name=chroma_name_for_tier(collection_id, tier))
        col.delete(ids=ids)

    def upsert(
        self,
        collection_id: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]] | None = None,
        tier: str = DEFAULT_TIER,
    ) -> None:
        """FIX-055/056: 同期 upsert (rag.py の col.upsert(...) 置換用)。

        embeddings 指定なら ChromaDB の内部 embedding_function を bypass し、
        呼出側 (FIX-056 で `_embedding_provider.embed` 経由) の値を使う。
        publish_collection_iter は同期ジェネレータのため async ではなく同期 API。

        FIX-056 完成: embeddings 指定時は collection 作成でも embedding_function=None を
        渡し、ChromaDB default embedding (all-MiniLM-L6-v2, 384 dim) を無効化して
        呼出側の embedding (BGE-M3 1024 dim) を尊重する。
        """
        if not ids:
            return
        # masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        if raw_tier_write_blocked("ChromaDBVectorStore.upsert", collection_id, tier, len(ids)):
            return
        client = self._ensure_client()
        _name = chroma_name_for_tier(collection_id, tier)
        if embeddings is not None:
            col = client.get_or_create_collection(name=_name, embedding_function=None)
        else:
            col = client.get_or_create_collection(name=_name)
        kwargs: dict = {"ids": ids, "documents": documents, "metadatas": metadatas}
        if embeddings is not None:
            kwargs["embeddings"] = embeddings
        col.upsert(**kwargs)

    def query_sync(
        self,
        collection_id: str,
        query_texts: list[str] | None = None,
        query_embeddings: list[list[float]] | None = None,
        n_results: int = 10,
        where: dict | None = None,
        include: list[str] | None = None,
        tier: str = DEFAULT_TIER,
    ) -> dict:
        """FIX-055: 同期 query (rag.py の col.query(...) 置換用)。

        既存の async search() は dict 形式を加工して返すが、本メソッドは
        rag.py の生 col.query 戻り値構造をそのまま透過する (置換時の互換性確保)。
        §段1c: tier で読み出し先を分岐 (既定 'raw')。
        """
        client = self._ensure_client()
        col = client.get_collection(name=chroma_name_for_tier(collection_id, tier))
        kwargs: dict = {"n_results": n_results}
        if query_texts is not None:
            kwargs["query_texts"] = query_texts
        if query_embeddings is not None:
            kwargs["query_embeddings"] = query_embeddings
        if where is not None:
            kwargs["where"] = where
        if include is not None:
            kwargs["include"] = include
        return col.query(**kwargs)

    async def export(self, collection_id: str, tier: str = DEFAULT_TIER) -> dict:
        client = self._ensure_client()
        try:
            col = client.get_collection(name=chroma_name_for_tier(collection_id, tier))
            data = col.get(include=["documents", "metadatas", "embeddings"])
            return {"collection_id": collection_id, "tier": tier, **data}
        except Exception as e:
            return {"collection_id": collection_id, "tier": tier, "error": str(e)}

    async def import_data(self, collection_id: str, data: dict, tier: str = DEFAULT_TIER) -> None:
        ids = data.get("ids") or []
        docs = data.get("documents") or []
        metas = data.get("metadatas") or [{} for _ in ids]
        embeds = data.get("embeddings")
        if not ids:
            return
        # masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        if raw_tier_write_blocked("ChromaDBVectorStore.import_data", collection_id, tier, len(ids)):
            return
        client = self._ensure_client()
        col = client.get_or_create_collection(name=chroma_name_for_tier(collection_id, tier))
        kwargs = {"ids": ids, "documents": docs, "metadatas": metas}
        if embeds:
            kwargs["embeddings"] = embeds
        col.upsert(**kwargs)

    async def test_connection(self) -> dict:
        try:
            client = self._ensure_client()
            cols = client.list_collections()
            return {"status": "connected", "provider": "chromadb", "path": self.path, "collections": len(cols)}
        except Exception as e:
            return {"status": "error", "provider": "chromadb", "path": self.path, "error": str(e)}


class QdrantVectorStore(VectorStoreProvider):
    """Qdrant サーバーへの接続Provider（骨格のみ・実動作は Qdrant 起動が前提）。"""

    def __init__(self, url: str = "http://localhost:6333", api_key: str = ""):
        self.url = (url or "http://localhost:6333").rstrip("/")
        self.api_key = api_key or os.environ.get("CYNOVELA_QDRANT_API_KEY", "") or ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["api-key"] = self.api_key
        return h

    async def add(self, collection_id: str, chunks: list[dict]) -> None:
        raise NotImplementedError("Qdrant: add は未実装（骨格のみ）")

    async def search(self, collection_id: str, query_embedding: list[float], n: int) -> list[dict]:
        raise NotImplementedError("Qdrant: search は未実装（骨格のみ）")

    async def delete_collection(self, collection_id: str) -> None:
        raise NotImplementedError("Qdrant: delete_collection は未実装（骨格のみ）")

    async def export(self, collection_id: str) -> dict:
        raise NotImplementedError("Qdrant: export は未実装（骨格のみ）")

    async def import_data(self, collection_id: str, data: dict) -> None:
        raise NotImplementedError("Qdrant: import_data は未実装（骨格のみ）")

    async def test_connection(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.url}/", headers=self._headers())
                if r.status_code < 500:
                    return {"status": "connected", "provider": "qdrant", "url": self.url}
                return {"status": "error", "provider": "qdrant", "url": self.url, "code": r.status_code}
        except Exception as e:
            return {"status": "disconnected", "provider": "qdrant", "url": self.url, "error": str(e)}


def get_vector_store_provider(config: dict) -> VectorStoreProvider:
    cfg = config or {}
    v = cfg.get("vector_store", {}) or {}
    provider = (v.get("provider") or "chromadb").lower()
    # Phase B: database.backend=postgres でも pgvector を選択（単一 swap-point 連動）。
    db_backend = ((cfg.get("database") or {}).get("backend") or "").lower()
    if provider == "pgvector" or db_backend in ("postgres", "postgresql", "pgvector"):
        from providers.pgvector_store import PgVectorStore

        return PgVectorStore()
    if provider == "qdrant":
        return QdrantVectorStore(url=v.get("qdrant_url", "http://localhost:6333"), api_key=v.get("qdrant_api_key", ""))
    return ChromaDBVectorStore(path=v.get("path", ""))


# ─── BLOCK A-4: 指示書互換 collection 単位 sync VectorStore ───
# 上記 VectorStoreProvider (async / 全コレクション横断) を尊重しつつ、
# 指示書の VectorStore (sync / 1コレクション固定) を追加する。
# 既存 rag.py の ChromaDB 直接呼び出しはそのまま維持し、新規コードはこちらを使う。


class VectorStore(ABC):
    """1つの ChromaDB コレクションに固定された sync インターフェイス。"""

    @abstractmethod
    def upsert(
        self, ids: list[str], embeddings: list[list[float]] | None, documents: list[str], metadatas: list[dict]
    ) -> None: ...

    @abstractmethod
    def query(self, query_embedding: list[float], n_results: int, where: Optional[dict] = None) -> dict: ...

    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...

    @abstractmethod
    def delete_by_metadata(self, where: dict) -> None: ...

    @abstractmethod
    def get_collection_info(self) -> dict: ...

    @abstractmethod
    def count(self, where: Optional[dict] = None) -> int: ...


class ChromaVectorStore(VectorStore):
    """1つの ChromaDB コレクションを sync でラップする。"""

    def __init__(self, collection):
        self._col = collection

    def upsert(self, ids, embeddings, documents, metadatas):
        kwargs = {"ids": ids, "documents": documents, "metadatas": metadatas}
        if embeddings is not None:
            kwargs["embeddings"] = embeddings
        self._col.upsert(**kwargs)

    def query(self, query_embedding, n_results, where=None):
        kwargs = dict(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
        if where:
            kwargs["where"] = where
        return self._col.query(**kwargs)

    def delete(self, ids):
        if ids:
            self._col.delete(ids=ids)

    def delete_by_metadata(self, where: dict) -> None:
        try:
            results = self._col.get(where=where, include=[])
            ids = results.get("ids") or []
            if ids:
                self._col.delete(ids=ids)
        except Exception:
            pass

    def get_collection_info(self) -> dict:
        try:
            return {"name": self._col.name, "count": self._col.count()}
        except Exception as e:
            return {"name": getattr(self._col, "name", "?"), "count": 0, "error": str(e)}

    def count(self, where: Optional[dict] = None) -> int:
        try:
            if where:
                results = self._col.get(where=where, include=[])
                return len(results.get("ids") or [])
            return self._col.count()
        except Exception:
            return 0


def get_chroma_vector_store(
    collection_name: str, path: str = "", tier: str = DEFAULT_TIER
) -> ChromaVectorStore:
    """指定 collection に対する sync VectorStore を返す (BLOCK A-4)。

    alpha §9-A-4: パッケージ配下 db/chroma を既定とする (rag.py CHROMA_PATH と整合)。
    新規コードは本関数を使う。既存の rag.py 直接呼び出しは維持する。
    §段1c: tier で {cid}__raw / {cid}__masked を分岐 (既定 'raw')。
    masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層は新規に「作らない」。tier が raw に
    解決される場合、既に在る __raw は開くが、無いときは作らずに masked 側を開く
    (呼び手 0 件の口だが、ここから空の __raw が生えるのを塞ぐ)。
    """
    import chromadb

    if not path:
        _vs_app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.expanduser(os.environ.get("CYNOVELA_CHROMA", os.path.join(_vs_app_dir, "db", "chroma")))
    _requested = chroma_name_for_tier(collection_name, tier)
    client = chromadb.PersistentClient(path=path)
    if _requested and _requested.endswith("__" + TIER_RAW):
        try:
            return ChromaVectorStore(client.get_collection(name=_requested))
        except Exception:
            _log.warning(
                "[Cynovela] masked-only: 伏字前の層 %s は作りません。伏字済み層を開きます。",
                _requested,
            )
            _requested = chroma_name_for_tier(
                _requested[: -len("__" + TIER_RAW)] or collection_name, TIER_MASKED
            )
    col = client.get_or_create_collection(name=_requested)
    return ChromaVectorStore(col)
