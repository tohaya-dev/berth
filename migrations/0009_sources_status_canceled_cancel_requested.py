"""migration 0009: sources.status の CHECK に 'canceled' を追加 + cancel_requested 列を追加。

C-11 (走査の中止): 従来、走査の中止はプロセス内フラグ (server._scan_cancel_flags) にしか
届かず、中止の終端も 'failed' で実失敗と区別できなかった。本マイグレーションで:
- 'canceled' 状態を CHECK に追加し、中止の終端を実失敗 ('failed') と区別できるようにする
- cancel_requested INTEGER DEFAULT 0 列を追加し、中止要求を DB 経由で
  worker プロセス (別プロセスの走査) へも届くようにする

変更前 CHECK: ('idle', 'scanning', 'completed', 'failed')
変更後 CHECK: ('idle', 'scanning', 'completed', 'failed', 'canceled')

SQLite では CHECK 変更に table 再作成が必要。
migration 0002 / 0008 (collection_state_widen / stopped_interrupted) と同じパターン。

なお sources は ALTER TABLE で後付けされたカラム (archived_at / archived_by) を持ち、
migration 0004 の UNIQUE index (uq_sources_name) も持つ。列は動的に検出して保全し、
index は再作成する (DROP TABLE で index も消えるため)。

Postgres 側: deploy/postgres/cynovela_pg_schema.sql は本マイグレーション適用後の
SQLite DB から tools/pg_consolidated_ddl.py で再生成が必要 (別途)。
"""

from __future__ import annotations

import re
import sqlite3

description = "sources.status の CHECK に 'canceled' を追加 + cancel_requested 列を追加 (走査中止の判別)"

_CHECK_OLD = "CHECK(status IN ('idle', 'scanning', 'completed', 'failed'))"
_CHECK_NEW = "CHECK(status IN ('idle', 'scanning', 'completed', 'failed', 'canceled'))"


def _table_sql(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()
    return row[0] if row else None


def _index_sqls(conn: sqlite3.Connection) -> list[str]:
    """sources に張られた明示 index の DDL (DROP TABLE で消えるため再作成用に控える)。"""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='sources' AND sql IS NOT NULL"
    ).fetchall()
    return [r[0] for r in rows]


def _rebuild(conn: sqlite3.Connection, new_sql: str, cols: list[str], status_expr: str | None = None) -> None:
    """sources を new_sql の定義で再作成し、全行を写す (0008 と同じパターン)。

    status_expr が与えられたら status 列だけその式で写す (rollback の丸め用)。
    """
    idx_sqls = _index_sqls(conn)
    new_sql = re.sub(
        r'CREATE TABLE\s+"?sources"?\s*\(',
        "CREATE TABLE sources_new (",
        new_sql,
        count=1,
    )
    col_list = ", ".join(cols)
    select_list = ", ".join(
        (status_expr if (c == "status" and status_expr) else c) for c in cols
    )
    # FK を一時無効化してテーブル再作成 (files / workspace_sources の参照を保全)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"""
{new_sql};
INSERT INTO sources_new ({col_list}) SELECT {select_list} FROM sources;
DROP TABLE sources;
ALTER TABLE sources_new RENAME TO sources;
""")
        # DROP TABLE で消えた index を作り直す (uq_sources_name 等)
        for _isql in idx_sqls:
            try:
                conn.execute(_isql)
            except Exception:
                pass
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def apply(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(sources)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        # sources テーブルが無い場合は何もしない (新規 DB 用に SCHEMA を使う)
        return

    old_sql = _table_sql(conn)
    if not old_sql:
        return

    # ---- (1) CHECK に 'canceled' を追加 (未追加のときだけ再作成 = 冪等) ----
    if "'canceled'" not in old_sql:
        new_sql = old_sql.replace(_CHECK_OLD, _CHECK_NEW)
        if new_sql == old_sql:
            # 空白の揺れ等で文字列一致しない DB にも対応 (status の CHECK を丸ごと置換)
            new_sql = re.sub(
                r"CHECK\s*\(\s*status\s+IN\s*\([^)]*\)\s*\)",
                _CHECK_NEW,
                old_sql,
                count=1,
            )
        _rebuild(conn, new_sql, cols)

    # ---- (2) cancel_requested 列を追加 (存在しないときだけ = 冪等) ----
    cols_after = [row[1] for row in conn.execute("PRAGMA table_info(sources)").fetchall()]
    if "cancel_requested" not in cols_after:
        conn.execute("ALTER TABLE sources ADD COLUMN cancel_requested INTEGER DEFAULT 0")


def rollback(conn: sqlite3.Connection) -> None:
    cursor = conn.execute("PRAGMA table_info(sources)")
    cols = [row[1] for row in cursor.fetchall()]
    if not cols:
        return

    # cancel_requested 列を落とす (SQLite 3.35+。失敗しても続行)
    if "cancel_requested" in cols:
        try:
            conn.execute("ALTER TABLE sources DROP COLUMN cancel_requested")
            cols = [c for c in cols if c != "cancel_requested"]
        except Exception:
            pass

    old_sql = _table_sql(conn)
    if not old_sql or "'canceled'" not in old_sql:
        return
    new_sql = old_sql.replace(_CHECK_NEW, _CHECK_OLD)
    if new_sql == old_sql:
        new_sql = re.sub(
            r"CHECK\s*\(\s*status\s+IN\s*\([^)]*\)\s*\)",
            _CHECK_OLD,
            old_sql,
            count=1,
        )
    # 'canceled' は 'failed' に丸めて退避 (CHECK 違反回避)
    _rebuild(
        conn,
        new_sql,
        cols,
        status_expr="CASE status WHEN 'canceled' THEN 'failed' ELSE status END",
    )
