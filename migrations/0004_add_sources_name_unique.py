"""migration 0004: sources.name に UNIQUE 制約を追加 (fix065-066 段 F)。

Cynovela では Data Source 名は人手で付与されるが、過去にスキャン中断やリトライで
同名 source が重複登録されることがあった (boot-src x4, gui-test-source x4 等)。
本マイグレーションで sources.name に UNIQUE index を張り、以後 INSERT 時点で
重複登録を防ぐ。

既存重複は db レベルで運用側 (本サイクル段 F の SQL) で MIN(id) を残して掃除済。
"""

from __future__ import annotations

import sqlite3

description = "sources.name に UNIQUE index uq_sources_name を追加"


def apply(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_name ON sources(name)")


def rollback(conn: sqlite3.Connection) -> None:
    conn.execute("DROP INDEX IF EXISTS uq_sources_name")
