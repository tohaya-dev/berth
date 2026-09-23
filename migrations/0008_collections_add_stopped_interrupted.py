"""migration 0008: collections.status の CHECK に 'stopped' と 'interrupted' を追加。

意味的に異なる 2 状態を共存させる:
- 'stopped': ユーザーが手動で停止した (VALID_STATE_TRANSITIONS に既存)
- 'interrupted': サーバー再起動により中断された (起動時回復経路で使用)

VALID_STATE_TRANSITIONS (routers/collections.py) に
    ("publishing", "stopped"): "publish 中断"
が定義されているが、collections.status の CHECK 制約には 'stopped' / 'interrupted' が
いずれも含まれていないため SQL レベルで弾かれていた。本マイグレーションでこれを解消する。

変更前 CHECK: ('draft', 'ingested', 'publishing', 'ready', 'failed')
変更後 CHECK: ('draft', 'ingested', 'publishing', 'ready', 'failed', 'stopped', 'interrupted')

SQLite では CHECK 変更に table 再作成が必要。
migration 0002 (collection_state_widen) と同じパターン。

なお、本テーブルは ALTER TABLE で後付けされたカラム
(allowed_roles_json / rag_strategy / chunk_size / chunk_overlap /
 rag_mode / acl_roles / last_published_at / archived_at / archived_by)
を持つ。これらを動的に検出して保全する。
"""

from __future__ import annotations

import re
import sqlite3

description = "collections.status の CHECK に 'stopped' / 'interrupted' を追加 (publish 中断状態 / サーバー再起動時回復)"


def apply(conn: sqlite3.Connection) -> None:
    # 既存運用 DB は collections テーブルが拡張カラムを持つため
    # 動的に列を取得して再作成する (migration 0002 と同じパターン)。
    cursor = conn.execute("PRAGMA table_info(collections)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        # collections テーブルが無い場合は何もしない (新規 DB 用に SCHEMA を使う)
        return

    col_defs_old = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='collections'"
    ).fetchone()
    if not col_defs_old:
        return
    old_sql = col_defs_old[0]

    # 冪等性確保: 既に 'stopped' かつ 'interrupted' を含む schema は no-op
    if "'stopped'" in old_sql and "'interrupted'" in old_sql:
        return

    # 'ingested' が含まれていない (migration 0002 未適用) ケースは想定外。
    # マイグレーションランナーは昇順適用なので 0002 完了後にここに来るはず。
    if "'ingested'" not in old_sql:
        # 念のため何もしない (上流マイグレーションが先に走る前提)
        return

    # 0002 完了後の標準形 → 0008 適用形
    new_sql = old_sql.replace(
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed'))",
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed', 'stopped', 'interrupted'))",
    )
    # 中間状態 (古い 0008 を 'stopped' だけで適用した DB) からのアップグレードにも対応
    new_sql = new_sql.replace(
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed', 'stopped'))",
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed', 'stopped', 'interrupted'))",
    )

    # ダブルクオート正規化された table 名 (例: "collections") への対応
    new_sql = re.sub(
        r'CREATE TABLE\s+"?collections"?\s*\(',
        "CREATE TABLE collections_new (",
        new_sql,
        count=1,
    )

    col_list = ", ".join(cols)
    # FK を一時無効化してテーブル再作成 (子テーブルの参照を保全)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""
{new_sql};
INSERT INTO collections_new ({col_list}) SELECT {col_list} FROM collections;
DROP TABLE collections;
ALTER TABLE collections_new RENAME TO collections;
""")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def rollback(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(collections)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        return
    col_defs = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='collections'"
    ).fetchone()
    if not col_defs:
        return
    old_sql = col_defs[0]

    new_sql = old_sql.replace(
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed', 'stopped', 'interrupted'))",
        "CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed'))",
    )
    new_sql = re.sub(
        r'CREATE TABLE\s+"?collections"?\s*\(',
        "CREATE TABLE collections_old (",
        new_sql,
        count=1,
    )

    col_list = ", ".join(cols)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # 'stopped' / 'interrupted' は 'failed' に丸めて退避 (CHECK 違反回避)
        conn.executescript(f"""
{new_sql};
INSERT INTO collections_old ({col_list}) SELECT
  {", ".join("CASE status WHEN 'stopped' THEN 'failed' WHEN 'interrupted' THEN 'failed' ELSE status END" if c == "status" else c for c in cols)}
FROM collections;
DROP TABLE collections;
ALTER TABLE collections_old RENAME TO collections;
""")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
