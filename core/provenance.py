"""Phase F: 軽い来歴（provenance）エンベロープ。

応答（message）に対し「誰が・どの埋め込みモデル版で・どのチャンクで・どのツールを叩いたか」を
既存テーブル（messages / sessions / message_rag_refs / document_provenance）から軽量に組み立てる
サイドカー JSON。重装備の新規ストアは作らない（指示書 §13）。

データの出所:
- 誰が        : sessions.user_id（message→session 経由）
- 埋め込み版  : message_rag_refs.vector_id の接尾辞（`...:baai_bge_m3_v1`）
- どのチャンク: message_rag_refs.logical_chunk_id（+ source_path / score / rank）
- どのツール  : messages.model_name（LLM）＋ 'rag_retrieve'（検索）
"""

from __future__ import annotations

from core.embedding_version import version_from_vector_id


def build_envelope(conn, message_id: str) -> dict:
    """指定 message の来歴エンベロープ（dict）を返す。応答に紐付く。"""
    msg = conn.execute(
        "SELECT id, session_id, role, model_name, created_at, redaction_status "
        "FROM messages WHERE id = ?",
        (message_id,),
    ).fetchone()
    if not msg:
        return {"message_id": message_id, "found": False}

    sess = conn.execute(
        "SELECT user_id, workspace_id FROM sessions WHERE id = ?", (msg["session_id"],)
    ).fetchone()

    refs = conn.execute(
        "SELECT logical_chunk_id, vector_id, rank, score, source_path "
        "FROM message_rag_refs WHERE message_id = ? ORDER BY rank",
        (message_id,),
    ).fetchall()

    versions = sorted({version_from_vector_id(r["vector_id"]) for r in refs if r["vector_id"]} - {""})
    chunks = [
        {
            "logical_chunk_id": r["logical_chunk_id"],
            "rank": r["rank"],
            "score": r["score"],
            "source_path": r["source_path"],
        }
        for r in refs
    ]
    tools = []
    if refs:
        tools.append("rag_retrieve")
    if msg["model_name"]:
        tools.append(f"llm:{msg['model_name']}")

    return {
        "message_id": message_id,
        "found": True,
        "who": (sess["user_id"] if sess else None),
        "workspace_id": (sess["workspace_id"] if sess else None),
        "embedding_versions": versions,
        "tools": tools,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "redaction_status": msg["redaction_status"],
        "created_at": msg["created_at"],
    }
