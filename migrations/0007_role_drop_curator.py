"""migration 0007: users.role から 'curator' を物理削除する。

fix-s2-4-curator-removal-v1 (Stage S2-4) で新設。

変更前: CHECK(role IN ('admin', 'curator', 'viewer'))
変更後: CHECK(role IN ('admin', 'viewer'))

ロール正規化:
- curator → viewer（既存 curator 行があれば一括 viewer に降格）
- admin / viewer は不変

SQLite では CHECK 制約を ALTER TABLE で変更できないため、新テーブル作成 → SELECT INSERT
→ 元テーブル削除 → RENAME の table 再作成方式を取る (0001 と同パターン)。

実 schema は運用で 10 カラム超に拡張されているため、PRAGMA table_info で動的に列を
取得して既存列構成を完全に維持する。
"""

from __future__ import annotations

import sqlite3

description = (
    "users.role を ('admin', 'viewer') に縮小、既存 curator を viewer に降格"
)


_ROLLBACK_SCRIPT = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE users_old (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'curator', 'viewer')),
    avatar TEXT,
    username      TEXT,
    display_name  TEXT,
    password_hash TEXT,
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT
);

INSERT INTO users_old (id, name, role, avatar, username, display_name, password_hash, is_active, created_at, updated_at)
SELECT
    id,
    name,
    role,
    avatar,
    username,
    display_name,
    password_hash,
    is_active,
    created_at,
    updated_at
FROM users;

DROP TABLE users;
ALTER TABLE users_old RENAME TO users;
"""


def apply(conn: sqlite3.Connection) -> None:
    # 動的列対応 (0001 と方式統一)。users テーブルの実列構成を
    # PRAGMA table_info で取得し、列追加状態でも整合性を保ったまま narrow する。
    cursor = conn.execute("PRAGMA table_info(users)")
    cols_info = cursor.fetchall()
    if not cols_info:
        # 新規 DB は SCHEMA 適用済みのため migration 不要
        return
    cols = [row[1] for row in cols_info]

    # 既に narrow 済み (role CHECK に curator 含まず) なら no-op
    table_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    table_sql = (table_sql_row[0] or "") if table_sql_row else ""
    if table_sql and "'curator'" not in table_sql:
        return

    # 既存 curator 行を viewer に降格 (新 CHECK 制約に適合させるため事前変換)
    conn.execute("UPDATE users SET role='viewer' WHERE role='curator'")

    # 動的に users_new を作成し、既存列を INSERT する。
    col_list = ", ".join(cols)
    # users_new の CREATE は固定列 + 後ろに動的列を追加する
    fixed_cols_def = (
        "id TEXT PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK(role IN ('admin', 'viewer')), "
        "avatar TEXT, "
        "username TEXT, "
        "display_name TEXT, "
        "password_hash TEXT, "
        "is_active INTEGER DEFAULT 1, "
        "created_at TEXT, "
        "updated_at TEXT"
    )
    fixed_cols = {
        "id",
        "name",
        "role",
        "avatar",
        "username",
        "display_name",
        "password_hash",
        "is_active",
        "created_at",
        "updated_at",
    }
    extra_defs: list[str] = []
    for row in cols_info:
        cname = row[1]
        if cname in fixed_cols:
            continue
        ctype = row[2] or "TEXT"
        extra_defs.append(f"{cname} {ctype}")
    extra_def_sql = (", " + ", ".join(extra_defs)) if extra_defs else ""

    select_cols = ", ".join(cols)

    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.executescript(
        f"CREATE TABLE users_new ({fixed_cols_def}{extra_def_sql});\n"
        f"INSERT INTO users_new ({col_list}) SELECT {select_cols} FROM users;\n"
        "DROP TABLE users;\n"
        "ALTER TABLE users_new RENAME TO users;\n"
    )


def rollback(conn: sqlite3.Connection) -> None:
    conn.executescript(_ROLLBACK_SCRIPT)
