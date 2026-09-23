"""migrations._runner — DB migration の適用ランナー。

各 migration は migrations/{番号}_{名前}.py に置く。モジュールは:
- `apply(conn)`: 適用処理
- `rollback(conn)`: ロールバック処理（dry-run なし、明示呼び出し）
- `description`: 1 行説明

`schema_migrations` テーブルに適用済みの id を記録する。
"""

from __future__ import annotations

import importlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("cynovela.migrations")

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT
);
"""


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_MIGRATIONS_DDL)
    conn.commit()


def _discover_migrations() -> list[str]:
    """migrations/ ディレクトリから {番号}_{名前}.py を昇順で列挙する。"""
    here = Path(__file__).parent
    files = [
        p.stem
        for p in sorted(here.glob("*.py"))
        if p.stem not in ("__init__", "_runner") and not p.stem.startswith("_")
    ]
    return files


def applied_ids(conn: sqlite3.Connection) -> set[str]:
    _ensure_table(conn)
    rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def pending(conn: sqlite3.Connection) -> list[str]:
    """未適用の migration id を順番通りに返す。"""
    applied = applied_ids(conn)
    return [m for m in _discover_migrations() if m not in applied]


def dry_run(conn: sqlite3.Connection) -> list[str]:
    """適用予定の migration id を返すのみ。conn は読み取りのみ。"""
    return pending(conn)


def apply_all(conn: sqlite3.Connection) -> list[str]:
    """未適用 migration を昇順で apply。各 migration を 1 トランザクションで処理。"""
    _ensure_table(conn)
    todo = pending(conn)
    done: list[str] = []
    for mig_id in todo:
        mod = importlib.import_module(f"migrations.{mig_id}")
        desc = getattr(mod, "description", "")
        logger.info(f"[migrations] applying {mig_id}: {desc}")
        try:
            mod.apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations (id, applied_at, description) VALUES (?, ?, ?)",
                (mig_id, datetime.now().isoformat(timespec="seconds"), desc),
            )
            conn.commit()
            done.append(mig_id)
            logger.info(f"[migrations] applied {mig_id}")
        except Exception as e:
            conn.rollback()
            logger.error(f"[migrations] FAILED {mig_id}: {e}")
            raise
    return done


def rollback_one(conn: sqlite3.Connection, mig_id: str) -> None:
    """指定の migration を rollback。schema_migrations から id を削除。"""
    _ensure_table(conn)
    mod = importlib.import_module(f"migrations.{mig_id}")
    logger.info(f"[migrations] rolling back {mig_id}")
    try:
        mod.rollback(conn)
        conn.execute("DELETE FROM schema_migrations WHERE id = ?", (mig_id,))
        conn.commit()
        logger.info(f"[migrations] rolled back {mig_id}")
    except Exception as e:
        conn.rollback()
        logger.error(f"[migrations] ROLLBACK FAILED {mig_id}: {e}")
        raise
