"""migration 0005: chunks/parent_chunks に tier 列を追加 (masking-rework-overnight-v5)。

PII マスキング再実装の土台となる「保管庫の階層分離」のためのスキーマ拡張。

設計判断（pageId 36694ef8-ac04-811c-9194-ce7d73a85098 「設計の正本」準拠）:
- 生本文 → tier='raw' 行（管理者保管庫 / Chroma {cid}__raw / BM25 raw 索引）
- マスク済本文 → tier='masked' 行（一般保管庫 / Chroma {cid}__masked / BM25 masked 索引）

入口の権限階層判定で tier='raw'/'masked' のどちらの行を引くかを構造的に決め、
表示時のロール書き分けに頼らない（生をベクトル化しないことで埋め込み逆変換を防ぐ）。

既存行はすべて tier='raw' とする（マスクなしで埋め込まれた既存データのため）。
段 3 で tier='raw' から派生して tier='masked' 行を作成し、二重化する。
"""

from __future__ import annotations

import sqlite3

description = "chunks/parent_chunks に tier 列追加 (raw/masked 階層分離)"


def _add_tier_column(conn: sqlite3.Connection, table: str) -> None:
    """ALTER TABLE {table} ADD COLUMN tier TEXT DEFAULT 'raw'。
    既に列がある場合は OperationalError を握り潰す（idempotent）。
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN tier TEXT DEFAULT 'raw'")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def apply(conn: sqlite3.Connection) -> None:
    _add_tier_column(conn, "chunks")
    _add_tier_column(conn, "parent_chunks")

    # 既存行に明示的に 'raw' を入れる（DEFAULT で入っているはずだが念のため）。
    # masking 前のデータは全て生本文なので raw として扱う。
    conn.execute("UPDATE chunks SET tier = 'raw' WHERE tier IS NULL OR tier = ''")
    conn.execute("UPDATE parent_chunks SET tier = 'raw' WHERE tier IS NULL OR tier = ''")

    # tier 別検索を高速化するためのインデックス（(collection_id, tier) 複合）。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chunks_collection_tier ON chunks(collection_id, tier)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_parent_collection_tier ON parent_chunks(collection_id, tier)"
    )


def rollback(conn: sqlite3.Connection) -> None:
    # SQLite は ALTER TABLE DROP COLUMN を 3.35+ でサポートするが、
    # 既存データを失う破壊操作なので index 削除のみ実施する安全 rollback。
    # 列自体は残しても他経路は tier 列を無視できる（NULL or 'raw' のままで動作）。
    conn.execute("DROP INDEX IF EXISTS idx_chunks_collection_tier")
    conn.execute("DROP INDEX IF EXISTS idx_parent_collection_tier")
