"""migration 0002: collections.status の CHECK に 'ingested' を追加 (Smart Ingestion Stage 2)。

Stage R7 C-4 で新設。Phase 3 Recon Agent J §1-3 中で「Stage 2/3 状態遷移 grep ヒット 0」と
指摘された機能を実装する。

状態遷移 (Smart Ingestion 仕様 / Notion 35994ef8 参照):
    draft → ingested → ready
            (Stage 2)   (Stage 3)

変更前 CHECK: ('draft', 'publishing', 'ready', 'failed')
変更後 CHECK: ('draft', 'ingested', 'publishing', 'ready', 'failed')

SQLite では CHECK 変更に table 再作成が必要。migration 0001 と同じパターン。
"""

from __future__ import annotations

import sqlite3

description = "collections.status の CHECK に 'ingested' を追加 (Smart Ingestion Stage 2 状態遷移)"


_APPLY_SCRIPT = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE collections_new (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed')),
    access_level TEXT DEFAULT 'public' CHECK(access_level IN ('public', 'internal', 'confidential')),
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

INSERT INTO collections_new SELECT id, name, workspace_id, status, access_level, chunk_count, created_at FROM collections;

DROP TABLE collections;
ALTER TABLE collections_new RENAME TO collections;
"""


_ROLLBACK_SCRIPT = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE collections_old (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'publishing', 'ready', 'failed')),
    access_level TEXT DEFAULT 'public' CHECK(access_level IN ('public', 'internal', 'confidential')),
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 'ingested' を 'draft' に戻して退避
INSERT INTO collections_old SELECT id, name, workspace_id,
    CASE status WHEN 'ingested' THEN 'draft' ELSE status END,
    access_level, chunk_count, created_at
FROM collections;

DROP TABLE collections;
ALTER TABLE collections_old RENAME TO collections;
"""


def apply(conn: sqlite3.Connection) -> None:
    # 既存運用 DB は collections テーブルが拡張カラム (chunk_count 等の後足し含む) を持つ可能性。
    # 安全のため動的に列を取得して再作成する。
    cursor = conn.execute("PRAGMA table_info(collections)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        # collections テーブルが無い場合は何もしない (新規 DB 用に SCHEMA を使う)
        return

    # 既存列を保持しつつ status の CHECK のみ緩和した新テーブルを作る
    # SQLite は ALTER TABLE での CHECK 変更不可のため、動的 SQL で対応
    col_defs_old = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='collections'").fetchone()
    if not col_defs_old:
        return
    old_sql = col_defs_old[0]
    # FIX-039: 既に 'ingested' を含む schema は no-op (冪等性確保)
    if "'ingested'" in old_sql:
        return
    new_sql = old_sql.replace(
        "CHECK(status IN ('draft', 'publishing', 'ready', 'failed'))",
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed'))",
    )
    # 念のため別パターン
    new_sql = new_sql.replace(
        "CHECK(status IN ('draft', 'publishing', 'ready', 'failed'))",
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed'))",
    )
    # FIX-039: ダブルクオート正規化 (1 回目 apply 後 sqlite_master.sql に "collections" 形式で保存される)
    # への対応として正規表現で TABLE 定義名を置換する
    import re as _re_a39

    new_sql = _re_a39.sub(
        r'CREATE TABLE\s+"?collections"?\s*\(',
        "CREATE TABLE collections_new (",
        new_sql,
        count=1,
    )

    col_list = ", ".join(cols)
    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.executescript(f"""
{new_sql};
INSERT INTO collections_new ({col_list}) SELECT {col_list} FROM collections;
DROP TABLE collections;
ALTER TABLE collections_new RENAME TO collections;
""")


def rollback(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(collections)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        return
    col_defs = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='collections'").fetchone()
    if not col_defs:
        return
    old_sql = col_defs[0]
    new_sql = old_sql.replace(
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed'))",
        "CHECK(status IN ('draft', 'publishing', 'ready', 'failed'))",
    )
    new_sql = new_sql.replace("CREATE TABLE collections", "CREATE TABLE collections_old")
    col_list = ", ".join(cols)
    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.executescript(f"""
{new_sql};
INSERT INTO collections_old ({col_list}) SELECT
  id, name, workspace_id,
  CASE status WHEN 'ingested' THEN 'draft' ELSE status END,
  access_level, chunk_count, created_at
FROM collections;
DROP TABLE collections;
ALTER TABLE collections_old RENAME TO collections;
""")
