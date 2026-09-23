"""migration 0003: chunks / parent_chunks / document_lineage / document_provenance に
collection_id → collections(id) ON DELETE CASCADE の FK を付与する。

FIX-041 (chunks) + FIX-042 (parent_chunks) + FIX-043 (document_lineage / document_provenance)
を集約。

SQLite では ALTER TABLE で FK 追加できないため、table 再作成方式を採る。
動的列対応 (PRAGMA table_info) で ALTER による列追加状態でも整合性を保つ
(FIX-040 と同方式)。冪等性: sqlite_master.sql に REFERENCES collections が
含まれていれば no-op。
"""

from __future__ import annotations

import sqlite3

description = (
    "chunks / parent_chunks / document_lineage / document_provenance に "
    "collection_id → collections(id) ON DELETE CASCADE の FK を付与"
)


# 各テーブルの (テーブル名, 固定列定義, 固定列名集合) を定義。
# extra 列は PRAGMA table_info から動的取得して末尾に追加する。
_TABLES = [
    (
        "chunks",
        (
            "chunk_id TEXT PRIMARY KEY, "
            "workspace_id TEXT NOT NULL, "
            "collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE, "
            "source_doc TEXT DEFAULT '', "
            "page_hint INTEGER, "
            "char_count INTEGER DEFAULT 0, "
            "pii_detected INTEGER DEFAULT 0, "
            "excluded INTEGER DEFAULT 0, "
            "content TEXT DEFAULT ''"
        ),
        {
            "chunk_id",
            "workspace_id",
            "collection_id",
            "source_doc",
            "page_hint",
            "char_count",
            "pii_detected",
            "excluded",
            "content",
        },
    ),
    (
        "parent_chunks",
        (
            "parent_id TEXT PRIMARY KEY, "
            "collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE, "
            "workspace_id TEXT NOT NULL, "
            "source_doc TEXT DEFAULT '', "
            "content TEXT NOT NULL, "
            "char_count INTEGER DEFAULT 0, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ),
        {
            "parent_id",
            "collection_id",
            "workspace_id",
            "source_doc",
            "content",
            "char_count",
            "created_at",
        },
    ),
    (
        "document_lineage",
        (
            "id TEXT PRIMARY KEY, "
            "file_id TEXT NOT NULL, "
            "workspace_id TEXT NOT NULL, "
            "collection_id TEXT REFERENCES collections(id) ON DELETE CASCADE, "
            "source_path TEXT NOT NULL, "
            "file_hash TEXT NOT NULL, "
            "file_size INTEGER, "
            "chunk_count INTEGER DEFAULT 0, "
            "publish_version INTEGER DEFAULT 1, "
            "acl_source TEXT DEFAULT 'cynovela', "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now'))"
        ),
        {
            "id",
            "file_id",
            "workspace_id",
            "collection_id",
            "source_path",
            "file_hash",
            "file_size",
            "chunk_count",
            "publish_version",
            "acl_source",
            "created_at",
            "updated_at",
        },
    ),
    (
        "document_provenance",
        (
            "id TEXT PRIMARY KEY, "
            "document_id TEXT NOT NULL, "
            "collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE, "
            "filename TEXT NOT NULL, "
            "sha256 TEXT NOT NULL, "
            "file_size INTEGER, "
            "version INTEGER NOT NULL DEFAULT 1, "
            "published_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "published_by TEXT NOT NULL DEFAULT 'unknown', "
            "is_current INTEGER NOT NULL DEFAULT 1"
        ),
        {
            "id",
            "document_id",
            "collection_id",
            "filename",
            "sha256",
            "file_size",
            "version",
            "published_at",
            "published_by",
            "is_current",
        },
    ),
]


def _has_fk_to_collections(conn: sqlite3.Connection, table_name: str) -> bool:
    """sqlite_master.sql に collections への REFERENCES が含まれているか判定。"""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
        (table_name,),
    ).fetchone()
    if not row or not row[0]:
        return False
    return "REFERENCES collections" in row[0]


def _add_fk_to_table(
    conn: sqlite3.Connection,
    table_name: str,
    fixed_cols_def: str,
    fixed_cols: set[str],
) -> None:
    """指定テーブルを再作成して FK を付与する (動的列対応)。"""
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    cols_info = cursor.fetchall()
    if not cols_info:
        # 新規 DB は SCHEMA / migrate_db で table 作成済み、それ以前なら何もしない
        return
    if _has_fk_to_collections(conn, table_name):
        return

    cols = [row[1] for row in cols_info]
    col_list = ", ".join(cols)

    # 動的列: 固定列に含まれないもの (将来 ALTER で追加された列) を末尾に維持
    extra_defs: list[str] = []
    for row in cols_info:
        cname = row[1]
        if cname in fixed_cols:
            continue
        ctype = row[2] or "TEXT"
        extra_defs.append(f"{cname} {ctype}")
    extra_def_sql = (", " + ", ".join(extra_defs)) if extra_defs else ""

    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.executescript(
        f"CREATE TABLE {table_name}_new ({fixed_cols_def}{extra_def_sql});\n"
        f"INSERT INTO {table_name}_new ({col_list}) SELECT {col_list} FROM {table_name};\n"
        f"DROP TABLE {table_name};\n"
        f"ALTER TABLE {table_name}_new RENAME TO {table_name};\n"
    )


def apply(conn: sqlite3.Connection) -> None:
    for table_name, fixed_cols_def, fixed_cols in _TABLES:
        _add_fk_to_table(conn, table_name, fixed_cols_def, fixed_cols)


def rollback(conn: sqlite3.Connection) -> None:
    # rollback は table 再作成で FK 削除する処理だが、運用上不要なので未実装。
    # FK 整合性確保が目的のため、rollback は基本的にしない。
    pass
