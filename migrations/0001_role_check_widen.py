"""migration 0001: users.role の CHECK 制約を緩和し、既存ロールを正規化する。

Stage R2-2 で新設。

変更前: CHECK(role IN ('admin', 'data-engineer', 'data-scientist'))
変更後: CHECK(role IN ('admin', 'curator', 'viewer'))

ロール正規化（A1 方針）:
- data-engineer → curator
- data-scientist → viewer
- admin → admin（不変）

SQLite では CHECK 制約を ALTER TABLE で変更できないため、新テーブル作成 → SELECT INSERT
→ 元テーブル削除 → RENAME の table 再作成方式を取る。

実 schema は運用で 10 カラムに拡張されている（avatar/username/display_name/password_hash
/is_active/created_at/updated_at が ALTER TABLE で追加済み）。新テーブルでも全カラムを保持する。
"""

from __future__ import annotations

import sqlite3

description = (
    "users.role を ('admin', 'curator', 'viewer') に緩和、既存 data-engineer/data-scientist を curator/viewer に正規化"
)


_APPLY_SCRIPT = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE users_new (
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

INSERT INTO users_new (id, name, role, avatar, username, display_name, password_hash, is_active, created_at, updated_at)
SELECT
    id,
    name,
    CASE role
        WHEN 'data-engineer' THEN 'curator'
        WHEN 'data-scientist' THEN 'viewer'
        ELSE role
    END AS role,
    avatar,
    username,
    display_name,
    password_hash,
    is_active,
    created_at,
    updated_at
FROM users;

DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
"""


_ROLLBACK_SCRIPT = """
PRAGMA defer_foreign_keys = ON;

CREATE TABLE users_old (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'data-engineer', 'data-scientist')),
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
    CASE role
        WHEN 'curator' THEN 'data-engineer'
        WHEN 'viewer' THEN 'data-scientist'
        ELSE role
    END AS role,
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
    # FIX-040: 動的列対応 (0002 と方式統一)。users テーブルの実列構成を
    # PRAGMA table_info で取得し、列追加状態でも整合性を保ったまま widen する。
    cursor = conn.execute("PRAGMA table_info(users)")
    cols_info = cursor.fetchall()
    if not cols_info:
        # 新規 DB は SCHEMA 適用済みのため migration 不要
        return
    cols = [row[1] for row in cols_info]

    # 既に widen 済み (role CHECK に curator / viewer 含む) なら no-op
    table_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if table_sql_row and "'curator'" in (table_sql_row[0] or ""):
        return

    # 動的に users_new を作成し、既存列を INSERT する。
    # role の旧→新変換は CASE で実施 (data-engineer→curator / data-scientist→viewer)。
    col_list = ", ".join(cols)
    # users_new の CREATE は固定列 + 後ろに動的列を追加する
    fixed_cols_def = (
        "id TEXT PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "role TEXT NOT NULL CHECK(role IN ('admin', 'curator', 'viewer')), "
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

    role_case = (
        "CASE role "
        "WHEN 'data-engineer' THEN 'curator' "
        "WHEN 'data-scientist' THEN 'viewer' "
        "ELSE role END AS role"
    )
    select_cols = ", ".join((role_case if c == "role" else c) for c in cols)

    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.executescript(
        f"CREATE TABLE users_new ({fixed_cols_def}{extra_def_sql});\n"
        f"INSERT INTO users_new ({col_list}) SELECT {select_cols} FROM users;\n"
        "DROP TABLE users;\n"
        "ALTER TABLE users_new RENAME TO users;\n"
    )


def rollback(conn: sqlite3.Connection) -> None:
    conn.executescript(_ROLLBACK_SCRIPT)
