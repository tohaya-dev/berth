"""migration 0006: chunks/parent_chunks に pii_summary 列を追加。

項目④ 検出結果の見える化（指示書 masking-alpha-finish-apply-autonomous-v2-20260522 / 研究メモ item4-metadata-research.md）。

取り込み時の検出結果から「種類 × 件数」のみを JSON で保存する（値は保存しない）。
例: '{"EMAIL": 3, "PHONE_JP": 1}'
"""

from __future__ import annotations

import sqlite3

description = "chunks/parent_chunks に pii_summary 列追加 (検出種別×件数 JSON)"


def _add_pii_summary_column(conn: sqlite3.Connection, table: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN pii_summary TEXT DEFAULT NULL")
    except sqlite3.OperationalError as e:
        if "duplicate column name" not in str(e).lower():
            raise


def apply(conn: sqlite3.Connection) -> None:
    _add_pii_summary_column(conn, "chunks")
    _add_pii_summary_column(conn, "parent_chunks")


def rollback(conn: sqlite3.Connection) -> None:
    pass
