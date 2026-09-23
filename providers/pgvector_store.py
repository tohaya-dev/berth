"""Cynovela — Postgres + pgvector VectorStoreProvider 実装（Phase B）。

ChromaDBVectorStore と同一の口（upsert / query_sync / delete_ids / delete_collection /
async add/search/export/import_data/test_connection）を pgvector で提供する。
rag.py は埋め込みを事前計算して渡す（FIX-056）ため、本実装も embeddings 必須で受け取り、
ChromaDB と同じ層（`collection` 列 = {cid}__masked）を表現する。
masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層（{cid}__raw）へは書き込まない
（判定は providers.vector_store.raw_tier_write_blocked を Chroma 側と共有）。
過去に入った __raw 行の読み出し・削除は今までどおり通す（既存データ互換）。

距離は cosine（Chroma の hnsw:space=cosine と一致）。pgvector `<=>`(vector_cosine_ops) は
cosine distance を返すため Chroma の distance と直接比較できる（厳密パリティ ε=1e-3 の前提）。

接続設定は yaml `database.postgres`（host/port/dbname/user）+ password は
マウントしたファイル（`password_file`）から読む。新規 os.environ/getenv は使わない。
"""

from __future__ import annotations

import json as _json
from typing import Optional

from providers.vector_store import (
    VectorStoreProvider,
    chroma_name_for_tier,
    raw_tier_write_blocked,
    DEFAULT_TIER,
    TIER_RAW,
    TIER_MASKED,
)

EMBED_DIM = 1024  # BGE-M3
TABLE = "chunks_vec"


def _resolve_pg_params(explicit: dict | None = None) -> dict:
    """pg 接続パラメータを解決する（explicit > yaml database.postgres）。

    password は `password`（明示）> `password_file`（マウントしたファイル）の順。
    env は使わない（指示書 env 全廃方針）。
    """
    params = {"host": "127.0.0.1", "port": 5433, "dbname": "cynovela", "user": "cynovela", "password": ""}
    cfg = {}
    try:
        from core.config import CYNOVELA_CONFIG as _DTC

        cfg = ((_DTC.get("database") or {}).get("postgres") or {})
    except Exception:
        cfg = {}
    _CONN_KEYS = ("host", "port", "dbname", "user", "password")
    for k in _CONN_KEYS:
        if cfg.get(k) not in (None, ""):
            params[k] = cfg[k]
    pwfile = cfg.get("password_file") or ""
    if (explicit or {}):
        params.update({k: v for k, v in explicit.items() if k in _CONN_KEYS and v not in (None, "")})
        pwfile = explicit.get("password_file") or pwfile
    if not params.get("password") and pwfile:
        try:
            with open(pwfile) as _f:
                params["password"] = _f.read().strip()
        except Exception:
            pass
    params["port"] = int(params["port"])
    return params


class PgVectorStore(VectorStoreProvider):
    """pgvector backend（ChromaDBVectorStore 互換の口）。"""

    def __init__(self, params: dict | None = None):
        self._params = _resolve_pg_params(params)
        self._ensured = False

    # ── 接続 ──
    def _connect(self):
        import psycopg

        return psycopg.connect(**self._params)

    def _ensure_schema(self):
        if self._ensured:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {TABLE} (
                        collection   text NOT NULL,
                        id           text NOT NULL,
                        document     text,
                        metadata     jsonb,
                        workspace_id text,
                        embedding    vector({EMBED_DIM}),
                        PRIMARY KEY (collection, id)
                    )
                    """
                )
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {TABLE}_ws_idx ON {TABLE} (collection, workspace_id)"
                )
            conn.commit()
        self._ensured = True

    def ensure_hnsw_index(self):
        """HNSW(cosine) インデックスを作成する（近似検索用・厳密パリティ確認後に呼ぶ）。"""
        self._ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE INDEX IF NOT EXISTS {TABLE}_hnsw_cos "
                    f"ON {TABLE} USING hnsw (embedding vector_cosine_ops)"
                )
            conn.commit()

    # ── sync 口（rag.py 用・ChromaDBVectorStore 互換） ──
    def upsert(
        self,
        collection_id: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]] | None = None,
        tier: str = DEFAULT_TIER,
    ) -> None:
        if not ids:
            return
        # masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        # Chroma 側 (ChromaDBVectorStore.upsert) と同一の判定を共有する。
        if raw_tier_write_blocked("PgVectorStore.upsert", collection_id, tier, len(ids)):
            return
        if embeddings is None:
            raise ValueError("PgVectorStore.upsert: embeddings は必須（呼出側で事前計算する）")
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        rows = []
        for i, _id in enumerate(ids):
            meta = metadatas[i] if i < len(metadatas) else {}
            ws = (meta or {}).get("workspace_id")
            emb = embeddings[i]
            emb = [float(x) for x in emb]
            rows.append((name, _id, documents[i] if i < len(documents) else "", _json.dumps(meta, ensure_ascii=False), ws, "[" + ",".join(repr(x) for x in emb) + "]"))
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    f"""
                    INSERT INTO {TABLE} (collection, id, document, metadata, workspace_id, embedding)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s::vector)
                    ON CONFLICT (collection, id) DO UPDATE SET
                        document=excluded.document, metadata=excluded.metadata,
                        workspace_id=excluded.workspace_id, embedding=excluded.embedding
                    """,
                    rows,
                )
            conn.commit()

    def query_sync(
        self,
        collection_id: str,
        query_texts: list[str] | None = None,
        query_embeddings: list[list[float]] | None = None,
        n_results: int = 10,
        where: dict | None = None,
        include: list[str] | None = None,
        tier: str = DEFAULT_TIER,
        exact: bool = False,
    ) -> dict:
        """Chroma 互換の戻り（ネスト list）。distance=cosine。

        exact=True で seqscan を強制し近似なし厳密検索を行う（パリティ確認用）。
        """
        if not query_embeddings:
            # rag.py は基本 query_embeddings を渡す。texts のみは未サポート（embedder 非保持）。
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]], "embeddings": [[]]}
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        qvec = "[" + ",".join(repr(float(x)) for x in query_embeddings[0]) + "]"
        want_emb = bool(include and "embeddings" in include)
        sql = (
            f"SELECT id, document, metadata, (embedding <=> %s::vector) AS distance"
            + (", embedding" if want_emb else "")
            + f" FROM {TABLE} WHERE collection = %s"
        )
        args: list = [qvec, name]
        ws = (where or {}).get("workspace_id") if where else None
        if ws:
            sql += " AND workspace_id = %s"
            args.append(ws)
        sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
        args += [qvec, int(n_results)]
        ids, docs, metas, dists, embs = [], [], [], [], []
        with self._connect() as conn:
            if exact:
                conn.execute("SET LOCAL enable_indexscan = off")
                conn.execute("SET LOCAL enable_bitmapscan = off")
            with conn.cursor() as cur:
                cur.execute(sql, args)
                for row in cur.fetchall():
                    ids.append(row[0])
                    docs.append(row[1])
                    metas.append(row[2] if isinstance(row[2], dict) else (_json.loads(row[2]) if row[2] else {}))
                    dists.append(float(row[3]))
                    if want_emb:
                        ev = row[4]
                        if isinstance(ev, str):
                            ev = [float(x) for x in ev.strip("[]").split(",") if x]
                        embs.append(list(ev) if ev is not None else [])
        out = {"ids": [ids], "documents": [docs], "metadatas": [metas], "distances": [dists]}
        if want_emb:
            out["embeddings"] = [embs]
        return out

    def delete_ids(self, collection_id: str, ids: list[str], tier: str = DEFAULT_TIER) -> None:
        if not ids:
            return
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TABLE} WHERE collection = %s AND id = ANY(%s)", (name, list(ids)))
            conn.commit()

    def count(self, collection_id: str, tier: str = DEFAULT_TIER) -> int:
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {TABLE} WHERE collection = %s", (name,))
                return int(cur.fetchone()[0])

    def scan_documents(self, collection_id: str, tier: str = DEFAULT_TIER) -> list[dict]:
        """bm25-index-source (DD-CYN-0116 X-2): 索引側に入っている document を全件返す。

        関係DB 側の本文が鍵不一致で復号できないとき、BM25 の代替ソースとして使う。
        埋め込みは読まない (BM25 に不要・転送量を増やさないため)。
        Chroma 版の `col.get(include=["documents","metadatas"])` と同じ役割。
        """
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        out: list[dict] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT id, document, metadata FROM {TABLE} WHERE collection = %s",
                    (name,),
                )
                for _id, _doc, _meta in cur.fetchall():
                    out.append({"id": _id, "document": _doc or "", "metadata": _meta or {}})
        return out

    # ── async 口（VectorStoreProvider 互換） ──
    async def add(self, collection_id: str, chunks: list[dict], tier: str = DEFAULT_TIER) -> None:
        if not chunks:
            return
        # masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        if raw_tier_write_blocked("PgVectorStore.add", collection_id, tier, len(chunks)):
            return
        self.upsert(
            collection_id,
            ids=[c.get("id", "") for c in chunks],
            documents=[c.get("document", "") for c in chunks],
            metadatas=[c.get("metadata", {}) for c in chunks],
            embeddings=[c.get("embedding") for c in chunks],
            tier=tier,
        )

    async def search(self, collection_id: str, query_embedding: list[float], n: int,
                     workspace_id: str | None = None, tier: str = DEFAULT_TIER) -> list[dict]:
        where = {"workspace_id": workspace_id} if workspace_id else None
        res = self.query_sync(collection_id, query_embeddings=[query_embedding], n_results=n, where=where, tier=tier)
        return [
            {"id": i, "document": d, "metadata": m, "distance": dist}
            for i, d, m, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])
        ]

    async def delete_collection(self, collection_id: str, tier: str | None = None) -> None:
        self._ensure_schema()
        targets = (
            [chroma_name_for_tier(collection_id, tier)]
            if tier in (TIER_RAW, TIER_MASKED)
            else [chroma_name_for_tier(collection_id, TIER_RAW), chroma_name_for_tier(collection_id, TIER_MASKED)]
        )
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {TABLE} WHERE collection = ANY(%s)", (targets,))
            conn.commit()

    async def export(self, collection_id: str, tier: str = DEFAULT_TIER) -> dict:
        self._ensure_schema()
        name = chroma_name_for_tier(collection_id, tier)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT id, document, metadata FROM {TABLE} WHERE collection = %s", (name,))
                rows = cur.fetchall()
        return {
            "collection_id": collection_id, "tier": tier,
            "ids": [r[0] for r in rows],
            "documents": [r[1] for r in rows],
            "metadatas": [r[2] for r in rows],
        }

    async def import_data(self, collection_id: str, data: dict, tier: str = DEFAULT_TIER) -> None:
        ids = data.get("ids") or []
        if not ids:
            return
        # masked-only §9-1 / DD-CYN-0115 M-7: 伏字前の層 (__raw) へは書かない。
        if raw_tier_write_blocked("PgVectorStore.import_data", collection_id, tier, len(ids)):
            return
        self.upsert(
            collection_id, ids=ids,
            documents=data.get("documents") or ["" for _ in ids],
            metadatas=data.get("metadatas") or [{} for _ in ids],
            embeddings=data.get("embeddings"),
            tier=tier,
        )

    async def test_connection(self) -> dict:
        try:
            self._ensure_schema()
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {TABLE}")
                    n = cur.fetchone()[0]
            return {"status": "connected", "provider": "pgvector",
                    "host": self._params.get("host"), "port": self._params.get("port"), "rows": int(n)}
        except Exception as e:
            return {"status": "error", "provider": "pgvector", "error": str(e)}
