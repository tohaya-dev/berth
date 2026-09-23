"""Phase E: 埋め込みモデルの版管理（無停止再インデックスの型）。

版はベクター metadata(`embedding_version`)＋vector_id 接尾辞(`...:baai_bge_m3_v1`)に保持され、
publish 来歴(document_provenance.embedding_version 列・Phase E で追加)にも第一級で記録する。

無停止再インデックスの型: 並走 → 比較(厳密パリティ ε) → 切替 → ロールバック窓。
本モジュールはその「比較」を担う再利用ヘルパー(parity_max_diff)と、版の inventory / backfill を提供する。
"""

from __future__ import annotations


def current_version() -> str:
    """現在有効な埋め込みモデルの版文字列（例 baai_bge_m3_v1）。"""
    try:
        import rag

        return rag._make_embedding_version(rag._current_embedding_model_name())
    except Exception:
        return "unknown"


def current_model() -> str:
    try:
        import rag

        return rag._current_embedding_model_name()
    except Exception:
        return "unknown"


def version_from_vector_id(vector_id: str) -> str:
    """vector_id(`logical:version`) から版を取り出す。"""
    if not vector_id or ":" not in vector_id:
        return ""
    return vector_id.rsplit(":", 1)[1]


def inventory_from_chroma(chroma_path: str) -> dict:
    """Chroma の全 collection を走査し metadata.embedding_version の分布を返す。"""
    import os
    os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
    import chromadb

    cli = chromadb.PersistentClient(path=chroma_path)
    dist: dict = {}
    for c in cli.list_collections():
        col = cli.get_collection(c.name)
        if col.count() == 0:
            continue
        g = col.get(include=["metadatas"])
        for m in (g.get("metadatas") or []):
            v = (m or {}).get("embedding_version") or "?"
            dist[v] = dist.get(v, 0) + 1
    return dist


def parity_max_diff(store_a, store_b, collection_id: str, query_embeddings: list, tier: str = "masked",
                    n_results: int = 10) -> float:
    """再インデックス前後（または 2 バックエンド）の vscore 厳密パリティ比較の再利用口。

    両 store の query_sync(exact) を chunk_id 単位で突合し、cosine distance の最大差を返す。
    停止ゲート: 返り値 <= eps(1e-3) なら版/バックエンド切替の安全条件を満たす。
    """
    def _q(store, qv):
        try:
            return store.query_sync(collection_id, query_embeddings=[qv], n_results=n_results, tier=tier, exact=True)
        except TypeError:
            return store.query_sync(collection_id, query_embeddings=[qv], n_results=n_results, tier=tier)

    mx = 0.0
    for qv in query_embeddings:
        ra = _q(store_a, qv)
        rb = _q(store_b, qv)
        da = {i: d for i, d in zip(ra["ids"][0], ra["distances"][0])}
        db_ = {i: d for i, d in zip(rb["ids"][0], rb["distances"][0])}
        for i in set(da) & set(db_):
            mx = max(mx, abs(float(da[i]) - float(db_[i])))
    return mx


def backfill_provenance(conn, version: str = "", model: str = "") -> int:
    """既存 document_provenance の embedding_version/model 列を現行版で補完する。

    publish 来歴に版が未記録（NULL）の行を現行版で埋める。更新件数を返す。
    """
    version = version or current_version()
    model = model or current_model()
    cur = conn.execute(
        "UPDATE document_provenance SET embedding_version = ?, embedding_model = ? "
        "WHERE embedding_version IS NULL OR embedding_version = ''",
        (version, model),
    )
    conn.commit()
    return cur.rowcount
