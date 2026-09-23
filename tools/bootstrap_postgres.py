"""Initialize an empty PostgreSQL installation without importing a user database."""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def main() -> int:
    from core.config import CYNOVELA_CONFIG
    if CYNOVELA_CONFIG.get("database", {}).get("backend") != "postgres":
        raise RuntimeError("PostgreSQL configuration is required")
    from core.relational_pg import apply_consolidated_ddl
    if apply_consolidated_ddl() <= 0:
        raise RuntimeError("Required PostgreSQL schema was not applied")
    import psycopg
    from providers.pgvector_store import _resolve_pg_params
    from db import hash_password
    with psycopg.connect(**_resolve_pg_params()) as conn:
        # Serialize concurrent installers; existing users and passwords are never changed.
        conn.execute("SELECT pg_advisory_xact_lock(74622001)")
        if conn.execute("SELECT count(*) FROM users").fetchone()[0]:
            print("Existing users retained; bootstrap is a no-op")
            return 0
        password = os.environ.get("CYNOVELA_ADMIN_INITIAL_PASSWORD", "")
        username = os.environ.get("CYNOVELA_ADMIN_USERNAME", "cynovela")
        if len(password) < 12:
            raise RuntimeError("Fresh install requires an initial admin password of at least 12 characters")
        conn.execute(
            "INSERT INTO users (id,name,display_name,role,username,password_hash,is_active,"
            "must_change_password,created_at,updated_at) "
            "VALUES ('user-admin','Admin','Admin','admin',%s,%s,1,0,"
            "to_char(now() at time zone 'utc','YYYY-MM-DD HH24:MI:SS'),"
            "to_char(now() at time zone 'utc','YYYY-MM-DD HH24:MI:SS'))",
            (username, hash_password(password)))
    print("Fresh PostgreSQL administrator initialized; no source database imported")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
