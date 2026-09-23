import sqlite3
import os
import uuid
import hashlib
import secrets
import json as _json
import unicodedata as _unicodedata
from datetime import datetime as _dt


def _nfc_path(p: str) -> str:
    """PORTABILITY FIX: file_path を NFC 正規化する。
    macOS(NFD) と Linux/Windows(NFC) 間でルックアップが揺れない様にする。"""
    return _unicodedata.normalize("NFC", p) if p else p

# FIX-4 (Critical): Mock版や独立配置のため、CYNOVELA_DB 環境変数でオーバーライド可能
# alpha §9-A-1: 既定はパッケージ配下の db/cynovela.db。env CYNOVELA_DB で上書き可。
_DB_APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.expanduser(os.environ.get("CYNOVELA_DB", os.path.join(_DB_APP_DIR, "db", "cynovela.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'viewer')),
    avatar TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT DEFAULT 'idle' CHECK(status IN ('idle', 'scanning', 'completed', 'failed')),
    file_count INTEGER DEFAULT 0,
    last_scanned TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER,
    mime_type TEXT,
    categories TEXT DEFAULT '[]',
    scanned_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS guardrail_policies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    rules TEXT NOT NULL DEFAULT '[]',
    state TEXT DEFAULT 'active' CHECK(state IN ('active', 'inactive')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    guardrail_policy_id TEXT REFERENCES guardrail_policies(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS workspace_sources (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_id, source_id)
);

-- P2-D: WS×複数Guardrailポリシーの中間テーブル（FK整合のため）
CREATE TABLE IF NOT EXISTS workspace_policies (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    policy_id    TEXT NOT NULL REFERENCES guardrail_policies(id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_id, policy_id)
);

CREATE TABLE IF NOT EXISTS workspace_users (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS collections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    status TEXT DEFAULT 'draft' CHECK(status IN ('draft', 'ingested', 'publishing', 'ready', 'failed')),
    access_level TEXT DEFAULT 'public' CHECK(access_level IN ('public', 'internal', 'confidential')),
    chunk_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collection_files (
    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    file_id TEXT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    PRIMARY KEY (collection_id, file_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    timestamp TEXT DEFAULT (datetime('now')),
    user_id TEXT,
    action TEXT NOT NULL,
    target TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- P2-B: Publish時のSHA256差分検知用（collection_id単位でハッシュとChromaDB上のchunk_idsを管理）
CREATE TABLE IF NOT EXISTS file_hashes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    chunk_ids     TEXT NOT NULL DEFAULT '[]',
    pdf_mode      TEXT NOT NULL DEFAULT 'fast',
    scanned_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(collection_id, file_path)
);

-- Stage-D #4: publish_jobs (バックグラウンドPublishの進捗管理)
-- status: pending / running / completed / failed / stopped
-- stage:  chunking / embedding / done / error / stopped
-- migrate_db() 内にも同じ CREATE TABLE IF NOT EXISTS が存在するが、
-- SCHEMA 単体で executescript できるようここに正本を置く。
CREATE TABLE IF NOT EXISTS publish_jobs (
    id            TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    status        TEXT DEFAULT 'pending',
    stage         TEXT DEFAULT '',
    progress      INTEGER DEFAULT 0,
    total         INTEGER DEFAULT 0,
    message       TEXT DEFAULT '',
    error         TEXT DEFAULT NULL,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""


def migrate_db(conn) -> None:
    """スキーマ変更はここに集約する。CREATE TABLE IF NOT EXISTSのみ許可する。"""
    conn.executescript(SCHEMA)

    # ---- Phase 1 追加: publish_history テーブル ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publish_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            doc_count   INTEGER DEFAULT 0,
            chunk_count INTEGER DEFAULT 0,
            pii_count   INTEGER DEFAULT 0,
            excluded_count INTEGER DEFAULT 0,
            avg_chunk_chars REAL DEFAULT 0,
            elapsed_seconds REAL DEFAULT 0
        )
    """)

    # ---- Phase 0c: publish_jobs (バックグラウンドPublishの進捗管理) ----
    # status: pending / running / completed / failed / stopped
    # stage:  chunking / embedding / done / error / stopped (publish_collection_iter の現在ステップ)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS publish_jobs (
            id            TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            status        TEXT DEFAULT 'pending',
            stage         TEXT DEFAULT '',
            progress      INTEGER DEFAULT 0,
            total         INTEGER DEFAULT 0,
            message       TEXT DEFAULT '',
            error         TEXT DEFAULT NULL,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    # 既存テーブルがある場合は stage 列を後足し (再起動時の互換)
    try:
        conn.execute("ALTER TABLE publish_jobs ADD COLUMN stage TEXT DEFAULT ''")
    except Exception:
        pass
    # Phase 2: 停止/キャンセルの DB フラグ化（プロセス内メモリ依存をやめ worker から共有）。
    # /publish/stop が cancel_requested=1 を立て、worker が定期ポーリングして中断する。
    try:
        conn.execute("ALTER TABLE publish_jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0")
    except Exception:
        pass

    # ---- Phase 1 追加: chunks テーブル（UIからの可視化用メタデータ） ----
    # v11はChromaDB主体だが、Chunksビューア／RAGデバッグ用にメタデータをSQLite側にも保持する。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            chunk_id      TEXT PRIMARY KEY,
            workspace_id  TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            source_doc    TEXT DEFAULT '',
            page_hint     INTEGER,
            char_count    INTEGER DEFAULT 0,
            pii_detected  INTEGER DEFAULT 0,
            excluded      INTEGER DEFAULT 0,
            content       TEXT DEFAULT ''
        )
    """)

    # 既存chunksテーブルに後から列を足す場合のALTER（存在時は無視）
    for col_def in [
        "ADD COLUMN pii_detected INTEGER DEFAULT 0",
        "ADD COLUMN excluded      INTEGER DEFAULT 0",
        "ADD COLUMN page_hint     INTEGER",
        "ADD COLUMN char_count    INTEGER DEFAULT 0",
        "ADD COLUMN source_doc    TEXT    DEFAULT ''",
    ]:
        try:
            conn.execute(f"ALTER TABLE chunks {col_def}")
        except Exception:
            pass  # カラムが既に存在する場合は無視

    # ---- PHASE UX-1: pipeline_presets (汎用ポリシーテンプレート) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pipeline_presets (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            description  TEXT,
            config_json  TEXT NOT NULL,
            is_builtin   INTEGER DEFAULT 0,
            created_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ---- PHASE UX-3: collection_locks (Publish 同時実行防止) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_locks (
            collection_id TEXT PRIMARY KEY,
            locked_by     TEXT,
            locked_at     TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ---- PHASE F-1: feedback (👍/👎 フィードバック収集) ----
    # 既存実装: id / message_id / rating / comment / created_at は保持。
    # PHASE F-1 で必要な列を ALTER TABLE で追加 (旧データは互換維持)。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id      TEXT,
            rating          INTEGER NOT NULL,
            comment         TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    for col_def in [
        "ADD COLUMN query_id          TEXT",
        "ADD COLUMN query             TEXT",
        "ADD COLUMN answer_preview    TEXT",
        "ADD COLUMN sources_used      TEXT",
        "ADD COLUMN mode              TEXT",
        "ADD COLUMN collection_id     TEXT",
        "ADD COLUMN workspace_id      TEXT",
        "ADD COLUMN response_time_ms  INTEGER",
        "ADD COLUMN crag_triggered    INTEGER DEFAULT 0",
        "ADD COLUMN multi_query_count INTEGER DEFAULT 1",
    ]:
        try:
            conn.execute(f"ALTER TABLE feedback {col_def}")
        except Exception:
            pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_rating ON feedback(rating)")

    # ---- PHASE B-4: processing_logs (Publish / RAG クエリの構造化ログ) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_logs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     TEXT NOT NULL DEFAULT (datetime('now')),
            log_type      TEXT NOT NULL,    -- 'publish' or 'rag_query'
            job_id        TEXT,             -- Publish job ID or query ID (任意)
            level         TEXT NOT NULL DEFAULT 'info',  -- info / warning / error / success
            message       TEXT NOT NULL,
            metadata_json TEXT              -- 任意: チャンク数 / 処理時間等
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proc_logs_type_ts ON processing_logs(log_type, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_proc_logs_job ON processing_logs(job_id)")

    # ---- PHASE A-3: parent_chunks (Parent-Child チャンキング) ----
    # Child チャンク (256tok) で検索し、Parent チャンク (1000tok) を LLM コンテキストに送る方式。
    # Child は ChromaDB に格納 (metadata に parent_id を付与)、Parent は SQLite に保存。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parent_chunks (
            parent_id     TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL,
            workspace_id  TEXT NOT NULL,
            source_doc    TEXT DEFAULT '',
            content       TEXT NOT NULL,
            char_count    INTEGER DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_collection ON parent_chunks(collection_id)")

    # ---- P2-5: document_lineage (Publish 来歴 / 差分検出基盤) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_lineage (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            collection_id TEXT,
            source_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size INTEGER,
            chunk_count INTEGER DEFAULT 0,
            publish_version INTEGER DEFAULT 1,
            acl_source TEXT DEFAULT 'cynovela',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_lineage_file_id ON document_lineage(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_lineage_workspace ON document_lineage(workspace_id)",
        "CREATE INDEX IF NOT EXISTS idx_lineage_hash ON document_lineage(file_hash)",
        "CREATE INDEX IF NOT EXISTS idx_lineage_path ON document_lineage(workspace_id, source_path)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    # ---- BLOCK B-1: collections.allowed_roles_json (ACL) ----
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN allowed_roles_json TEXT")
    except Exception:
        pass

    # ---- P2-6: collections.rag_strategy (Collection ごとの RAG 戦略) ----
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN rag_strategy TEXT DEFAULT 'hybrid_bm25'")
    except Exception:
        pass

    # ---- GUI修正(2026-05-01) #6: コレクション単位のチャンキング設定上書き ----
    # 空 (NULL) の場合はグローバル設定 (settings.chunking.*) を使用する fallback 動作。
    for _col_def in (
        "ALTER TABLE collections ADD COLUMN chunk_size INTEGER",
        "ALTER TABLE collections ADD COLUMN chunk_overlap INTEGER",
        "ALTER TABLE collections ADD COLUMN rag_mode TEXT",
    ):
        try:
            conn.execute(_col_def)
        except Exception:
            pass

    # ---- collections.raw_only 列 (2026-07-09 追加 / 機能は 2026-07-24 廃止) ----
    # 伏字なし取り込みは廃止済み (vector-tier-masked-only-20260724 §9-7 / DD-CYN-0115 M-7)。
    # 列と過去データは保全のため残す (消すと既存の記録が壊れうる)。新規に 1 が書かれる経路は無い。
    # 残った 1 は publish の外部送出フェイルクローズ (rag.py) の判定にだけ使われる。
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN raw_only INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # ---- DD-CYN-0132 4-4: collections.egress_allowed 列 ----
    # コレクションを「外へ出してよいか」の印。既定は 0 = 外へ出さない (fail-safe)。
    # API はこの印を返すだけで、宛先の判定 (実際に送るか) はしない (判定は別層の役目)。
    # 管理者が明示的に 1 を立てたコレクションのみ、将来の外向き経路で共有可の候補になる。
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN egress_allowed INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    # ---- P1 §5-4: sessions に token_usage (JSON) 列を追加 ----
    # 構造例: {"model_name": "...", "tokens_total_session": 1234,
    #         "tokens_input": 600, "tokens_output": 634,
    #         "context_limit": 32768, "context_used_pct": 3.8}
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN token_usage TEXT")
    except Exception:
        pass

    # ---- DD-CYN-0132 4-2: 用途を限定した鍵 (api_keys) ----
    # 自動化 (CLI/MCP) 用の資格。管理者の合言葉を配らずに、用途を絞った鍵で API を叩けるようにする。
    #   key_hash: 平文鍵は保存せず sha256 のみ保存 (発行時に平文を1回だけ返す)。
    #   user_id / role: 鍵が名乗る利用者と役割 (発行者の役割を上限に、下げる方向のみ)。
    #   scope_json: {"workspaces": [...], "collections": [...], "tools": [...]} で
    #     引ける範囲を絞る。空リスト/未設定は「その軸で絞らない (全域)」を意味する。
    #   revoked_at: 失効時刻。非 NULL なら受理しない。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            key_prefix   TEXT NOT NULL DEFAULT '',
            key_hash     TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            role         TEXT NOT NULL DEFAULT 'viewer',
            scope_json   TEXT NOT NULL DEFAULT '{}',
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            created_by   TEXT NOT NULL DEFAULT 'unknown',
            last_used_at TEXT,
            revoked_at   TEXT
        )
    """)

    # ---- P4 Block 1: Document Provenance (publish 時の sha256 + version 履歴) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS document_provenance (
            id            TEXT PRIMARY KEY,
            document_id   TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            filename      TEXT NOT NULL,
            sha256        TEXT NOT NULL,
            file_size     INTEGER,
            version       INTEGER NOT NULL DEFAULT 1,
            published_at  TEXT NOT NULL DEFAULT (datetime('now')),
            published_by  TEXT NOT NULL DEFAULT 'unknown',
            is_current    INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_provenance_collection " "ON document_provenance(collection_id, document_id)"
    )
    # ---- Phase E: 埋め込みモデル版管理（無停止再インデックスの型の記録列） ----
    # 版はベクター metadata(embedding_version) にも保持されるが、publish 来歴にも第一級で記録する。
    for _pv_col in (
        "ALTER TABLE document_provenance ADD COLUMN embedding_version TEXT",
        "ALTER TABLE document_provenance ADD COLUMN embedding_model   TEXT",
    ):
        try:
            conn.execute(_pv_col)
        except Exception:
            pass

    # ---- P4 Block 2: Admin change log ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_change_log (
            id           TEXT PRIMARY KEY,
            timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
            changed_by   TEXT NOT NULL DEFAULT 'unknown',
            entity_type  TEXT NOT NULL,
            entity_id    TEXT,
            action       TEXT NOT NULL,
            before_value TEXT,
            after_value  TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_log_ts " "ON admin_change_log(timestamp DESC)")

    # ---- P5 Block 2: Self-observation reports (LLM 生成サマリー) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id           TEXT PRIMARY KEY,
            report_type  TEXT NOT NULL,
            content      TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            generated_by TEXT NOT NULL DEFAULT 'unknown',
            days_covered INTEGER NOT NULL DEFAULT 30
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_ts " "ON reports(generated_at DESC)")

    # ---- P4 Block 5: Blocked topics (Guardrail) ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blocked_topics (
            id         TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            pattern    TEXT NOT NULL,
            is_regex   INTEGER NOT NULL DEFAULT 0,
            action     TEXT NOT NULL DEFAULT 'block',
            created_by TEXT NOT NULL DEFAULT 'unknown',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            is_active  INTEGER NOT NULL DEFAULT 1
        )
    """)

    # ---- P4 Block 6: files テーブルにビジネスメタデータ + 自動分類列を追加 ----
    # 既存の files テーブル (本コードベースで documents 相当) を ALTER。
    # 既に存在するカラムは IGNORE。
    for _alter in (
        "ALTER TABLE files ADD COLUMN owner TEXT",
        "ALTER TABLE files ADD COLUMN department TEXT",
        "ALTER TABLE files ADD COLUMN project TEXT",
        "ALTER TABLE files ADD COLUMN doc_type TEXT",
        "ALTER TABLE files ADD COLUMN sensitivity_level TEXT",
        "ALTER TABLE files ADD COLUMN sensitivity_score INTEGER",
        "ALTER TABLE files ADD COLUMN metadata_enriched_at TEXT",
        "ALTER TABLE files ADD COLUMN refreshed_at TEXT",
    ):
        try:
            conn.execute(_alter)
        except Exception:
            pass

    # ---- Smart Ingestion (S-1): 自動分類結果の保存列 ----
    # classification: JSON {"category", "confidence", "tags", "classified_by"}
    # classified_at:  分類実行時刻 (ISO8601)
    for _alter in (
        "ALTER TABLE files ADD COLUMN classification TEXT DEFAULT NULL",
        "ALTER TABLE files ADD COLUMN classified_at TEXT DEFAULT NULL",
    ):
        try:
            conn.execute(_alter)
        except Exception:
            pass

    # ---- intake-togo-v2-20260705 (Fix 7 差分同期): 実体消滅フラグ ----
    # missing=1: 再スキャン時に取り込みフォルダから実体が見つからなかったファイル。
    # 行は削除しない（非破壊）。実体が再出現したらスキャン時の upsert で 0 に戻る。
    for _alter in (
        "ALTER TABLE files ADD COLUMN missing INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(_alter)
        except Exception:
            pass

    # ---- audit_logs 拡張（ip_address / result / category） ----
    for _alter in (
        "ALTER TABLE audit_logs ADD COLUMN ip_address TEXT DEFAULT NULL",
        "ALTER TABLE audit_logs ADD COLUMN result TEXT DEFAULT NULL",
        "ALTER TABLE audit_logs ADD COLUMN category TEXT DEFAULT NULL",
    ):
        try:
            conn.execute(_alter)
        except Exception:
            pass

    # audit_logs パフォーマンス改善（timestamp インデックス）
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp " "ON audit_logs (timestamp DESC)")
    except Exception:
        pass

    # ---- BLOCK A-3: sessions / messages / message_rag_refs / system_prompts / feedback ----
    # 既存テーブル (audit_logs, files) は触らず、新規テーブルだけ追加する。
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_prompts (
            id TEXT PRIMARY KEY,
            prompt_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL DEFAULT 'v1',
            prompt_text TEXT NOT NULL,
            prompt_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            system_prompt_id TEXT,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            model_name TEXT,
            redaction_status TEXT NOT NULL DEFAULT 'clean',
            pii_flags_json TEXT NOT NULL DEFAULT '[]',
            token_count INTEGER,
            latency_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS message_rag_refs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL,
            logical_chunk_id TEXT NOT NULL,
            vector_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            score REAL,
            source_path TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # 検索用インデックス
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_rag_refs_message ON message_rag_refs(message_id, rank)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_ws_user ON sessions(workspace_id, user_id)",
        "CREATE INDEX IF NOT EXISTS idx_feedback_message ON feedback(message_id)",
    ]:
        try:
            conn.execute(idx_sql)
        except Exception:
            pass

    # ---- P4-2: messages.retrieval_json (citations / pipeline_detail を履歴復元用に永続化) ----
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN retrieval_json TEXT")
    except Exception:
        pass

    # ---- P4-11: workspaces.sync_config (WS単位の自動ポーリング設定) ----
    try:
        conn.execute("ALTER TABLE workspaces ADD COLUMN sync_config TEXT DEFAULT NULL")
    except Exception:
        pass

    # ---- P4-6: workspaces.description / updated_at (編集機能用) ----
    try:
        conn.execute("ALTER TABLE workspaces ADD COLUMN description TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE workspaces ADD COLUMN updated_at TEXT")
    except Exception:
        pass

    # ---- P5-A: ACL — chunks.acl_roles (JSON list mirror of ChromaDB metadata) ----
    try:
        conn.execute("ALTER TABLE chunks ADD COLUMN acl_roles TEXT DEFAULT NULL")
    except Exception:
        pass
    # ---- P5-A: workspaces.acl_config (JSON: 既定ACL等のWS横断設定) ----
    try:
        conn.execute("ALTER TABLE workspaces ADD COLUMN acl_config TEXT DEFAULT NULL")
    except Exception:
        pass
    # ---- P5-A: collections.acl_roles (JSON list — allowed_roles_json と同期する正規列) ----
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN acl_roles TEXT DEFAULT NULL")
    except Exception:
        pass
    # ---- GUI修正 #5: collections.last_published_at ----
    try:
        conn.execute("ALTER TABLE collections ADD COLUMN last_published_at TEXT DEFAULT NULL")
    except Exception:
        pass
    # ---- フェーズ2: chunks.context_text (Contextual Chunking 用) ----
    try:
        conn.execute("ALTER TABLE chunks ADD COLUMN context_text TEXT DEFAULT NULL")
    except Exception:
        pass
    # ---- GUI修正2 #35: アーカイブ機能 (sources/workspaces/collections) ----
    for tbl in ("sources", "workspaces", "collections"):
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN archived_at TEXT DEFAULT NULL")
        except Exception:
            pass
        # UX-4: archived_by 列も追加 (誰がアーカイブしたかを記録)
        try:
            conn.execute(f"ALTER TABLE {tbl} ADD COLUMN archived_by TEXT")
        except Exception:
            pass
    # ---- GUI修正2 #34: chunks.last_accessed_at (RAG検索でヒット時に更新) ----
    try:
        conn.execute("ALTER TABLE chunks ADD COLUMN last_accessed_at TEXT DEFAULT NULL")
    except Exception:
        pass

    # ---- P5-A: acl_users — デモ用ACLロール定義テーブル ----
    conn.execute("""
        CREATE TABLE IF NOT EXISTS acl_users (
            id          TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            role        TEXT NOT NULL,
            department  TEXT DEFAULT '',
            avatar      TEXT DEFAULT '',
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # DD-CYN-0021 §2-1: acl_users.role は利用者ロール値を入れる列 (文書側の ACL は
    # collections.acl_roles / chunks.acl_roles が別に持ち、本表を参照しない)。
    # 撤去済み職能名の seed 行を削除し、有効ロール
    # (core/constants.py VALID_ROLES) のものだけ残す。既存 DB の行は触らない
    # (本表を読む経路が無いため挙動不変)。
    for u in [
        ("acl-admin", "管理者", "admin", "全社", "👑"),
    ]:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO acl_users (id, display_name, role, department, avatar) "
                "VALUES (?, ?, ?, ?, ?)",
                u,
            )
        except Exception:
            pass

    # ---- P5-B: documents/files メタデータエンジン拡張 ----
    for col_def in [
        "ADD COLUMN doc_type          TEXT DEFAULT ''",
        "ADD COLUMN sensitivity       TEXT DEFAULT 'public'",
        "ADD COLUMN sensitivity_score REAL DEFAULT 0",
        "ADD COLUMN auto_tags         TEXT DEFAULT '[]'",
        "ADD COLUMN owner             TEXT DEFAULT ''",
        "ADD COLUMN department        TEXT DEFAULT ''",
        "ADD COLUMN freshness_days    INTEGER DEFAULT NULL",
        "ADD COLUMN expires_at        TEXT DEFAULT NULL",
    ]:
        try:
            conn.execute(f"ALTER TABLE files {col_def}")
        except Exception:
            pass

    # ---- BLOCK 2: usersテーブル拡張 (動的ユーザー管理) ----
    # 既存users行を壊さないよう、ADD COLUMNで段階追加。
    # roleのCHECK制約は既存のまま（admin/viewer）を維持する。
    for col_def in [
        "ADD COLUMN username      TEXT",
        "ADD COLUMN display_name  TEXT",
        "ADD COLUMN password_hash TEXT",
        "ADD COLUMN is_active     INTEGER DEFAULT 1",
        "ADD COLUMN created_at    TEXT",
        "ADD COLUMN updated_at    TEXT",
    ]:
        try:
            conn.execute(f"ALTER TABLE users {col_def}")
        except Exception:
            pass

    # release/v1.0.0-alpha: 既存ユーザー初期化時、固定 "admin" ではなく毎回ランダム値で hash。
    # 結果として password_hash が NULL でない既存行はそのまま、NULL の行のみ届かない
    # ランダムハッシュで初期化される (実質的にログイン不可)。
    # 本番投入時は CYNOVELA_ADMIN_INITIAL_PASSWORD env を設定して user-admin を別経路で seed する。
    default_hash = hash_password(secrets.token_urlsafe(16))
    now = _dt.now().isoformat(timespec="seconds")
    rows = conn.execute("SELECT id, name, username, display_name, password_hash FROM users").fetchall()
    for r in rows:
        updates = []
        params: list = []
        if not r["username"]:
            updates.append("username = ?")
            params.append(r["id"])
        if not r["display_name"]:
            updates.append("display_name = ?")
            params.append(r["name"])
        if not r["password_hash"]:
            updates.append("password_hash = ?")
            params.append(default_hash)
        updates.append("is_active = COALESCE(is_active, 1)")
        if not updates:
            continue
        params.append(r["id"])
        conn.execute(
            f"UPDATE users SET {', '.join(updates)}, created_at = COALESCE(created_at, '{now}'), "
            f"updated_at = COALESCE(updated_at, '{now}') WHERE id = ?",
            params,
        )

    # Batch-B S1-1: 初回ログイン時のパスワード変更強制フラグ
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
        )
    except Exception:
        pass

    # agentK K-1 (q5 B1): パスワード変更時刻 (UTC epoch 秒)。これより前に発行された
    # アクセストークン (JWT iat) を core/auth.py _require_authenticated が拒否する。
    # NULL = 未変更 (従来どおり通す)。Postgres 側は deploy/postgres/cynovela_pg_schema.sql。
    try:
        conn.execute(
            "ALTER TABLE users ADD COLUMN password_changed_at INTEGER"
        )
    except Exception:
        pass

    # Batch-B S1-3: JWT リフレッシュトークン管理テーブル
    conn.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rt_user ON refresh_tokens(user_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_rt_expires ON refresh_tokens(expires_at)"
    )

    # PDF-mode 差分: file_hashes に pdf_mode 列を後足し（既存DB互換、存在時は無視）
    try:
        conn.execute(
            "ALTER TABLE file_hashes ADD COLUMN pdf_mode TEXT NOT NULL DEFAULT 'fast'"
        )
    except Exception:
        pass

    conn.commit()


# ─── BLOCK 2: パスワードハッシュ／検証 ───


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return f"{salt}:{h.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash or ":" not in stored_hash:
        return False
    salt, expected = stored_hash.split(":", 1)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000)
    return secrets.compare_digest(h.hex(), expected)


# ─── Phase A: 関係データ・バックエンド差し替え口（挙動不変） ───
# 既定は SQLite（現行挙動をそのまま維持）。cynovela.yaml `database.backend` で選択。
# Phase B で Postgres 実装（関係＋ベクターを同一接続へ統合）を追加する。
class RelationalBackend:
    """関係データ接続を生成する差し替え口。"""

    def connect(self):  # -> sqlite3.Connection | psycopg.Connection (Phase B)
        raise NotImplementedError


class SQLiteBackend(RelationalBackend):
    """現行 SQLite 接続をそのまま提供する既定バックエンド（挙動不変）。

    DB_PATH（module global・env CYNOVELA_DB）を毎回参照する。テストの
    monkeypatch(db.DB_PATH) 互換のため path はキャッシュしない。
    """

    def connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        # check_same_thread=False: FastAPIのStreamingResponseが同期ジェネレータを
        # threadpoolで回すため、接続生成スレッドと利用スレッドが異なるケースがある。
        # WAL + 単接続使い切り（open/close）。バックグラウンド書き込みとの競合は
        # busy_timeout で吸収する。
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        # Phase 0c B-1: 並行書き込み時の "database is locked" 即時失敗を防ぐ。
        # 30000ms (30秒) 待ってから OperationalError を上げる (_do_scan / _run_publish_background
        # 等の長時間スレッドの commit を含む短時間ロックを吸収する)。
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn


_RELATIONAL_BACKEND: "RelationalBackend | None" = None


def _select_relational_backend() -> "RelationalBackend":
    """cynovela.yaml `database.backend` で関係バックエンドを選択する。
    既定 'sqlite'（現行挙動）。未知/失敗時も SQLite に安全側フォールバック。"""
    backend = "sqlite"
    try:
        from core.config import CYNOVELA_CONFIG as _DTC

        backend = ((_DTC.get("database") or {}).get("backend") or "sqlite").lower()
    except Exception:
        backend = "sqlite"
    if backend in ("postgres", "postgresql", "pgvector"):
        try:
            from core.relational_pg import PostgresBackend  # Phase B で追加

            return PostgresBackend()
        except Exception:
            # Phase A 時点では未実装 → SQLite で安全側フォールバック
            return SQLiteBackend()
    return SQLiteBackend()


def get_relational_backend() -> "RelationalBackend":
    """現在有効な関係バックエンドを返す（lazy・キャッシュ）。"""
    global _RELATIONAL_BACKEND
    if _RELATIONAL_BACKEND is None:
        _RELATIONAL_BACKEND = _select_relational_backend()
    return _RELATIONAL_BACKEND


def set_relational_backend(backend: "RelationalBackend | None") -> None:
    """テスト / Phase B 用に関係バックエンドを差し替える（None でリセット）。"""
    global _RELATIONAL_BACKEND
    _RELATIONAL_BACKEND = backend


def get_db() -> sqlite3.Connection:
    """関係データ接続を返す（差し替え口経由・既定 SQLite で挙動不変）。"""
    return get_relational_backend().connect()


def new_id() -> str:
    return uuid.uuid4().hex[:16]


# ─── file_hashes CRUD（P2-B: SHA256差分スキャン） ───


def get_file_hash(conn, collection_id: str, file_path: str):
    file_path = _nfc_path(file_path)
    row = conn.execute(
        "SELECT * FROM file_hashes WHERE collection_id=? AND file_path=?",
        (collection_id, file_path),
    ).fetchone()
    return dict(row) if row else None


def upsert_file_hash(
    conn,
    collection_id: str,
    file_path: str,
    sha256: str,
    chunk_ids: list,
    pdf_mode: str = "fast",
) -> None:
    file_path = _nfc_path(file_path)
    conn.execute(
        """
        INSERT INTO file_hashes (collection_id, file_path, sha256, chunk_ids, pdf_mode, scanned_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(collection_id, file_path) DO UPDATE SET
            sha256=excluded.sha256,
            chunk_ids=excluded.chunk_ids,
            pdf_mode=excluded.pdf_mode,
            scanned_at=excluded.scanned_at
        """,
        (collection_id, file_path, sha256, _json.dumps(chunk_ids), pdf_mode),
    )
    conn.commit()


def find_file_hash_by_sha(conn, collection_id: str, sha256: str, exclude_path: str = ""):
    """DD-CYN-0136 2-4: 同一 collection 内で同じ SHA256 を持つ別パスの行を返す (無ければ None)。

    同一内容・別ファイル名の重複取込を publish 時に検出する用途。鍵が
    (collection_id, file_path) のため、従来は内容が同じでも名前が違えば素通りだった。"""
    exclude_path = _nfc_path(exclude_path)
    row = conn.execute(
        "SELECT * FROM file_hashes WHERE collection_id=? AND sha256=? AND file_path<>? LIMIT 1",
        (collection_id, sha256, exclude_path),
    ).fetchone()
    return dict(row) if row else None


def delete_file_hash(conn, collection_id: str, file_path: str) -> None:
    file_path = _nfc_path(file_path)
    conn.execute(
        "DELETE FROM file_hashes WHERE collection_id=? AND file_path=?",
        (collection_id, file_path),
    )
    conn.commit()


def list_file_hashes(conn, collection_id: str) -> list:
    rows = conn.execute(
        "SELECT * FROM file_hashes WHERE collection_id=?",
        (collection_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _rebase_relative_files(conn) -> int:
    """dist-file-id-rebase-20260802 (DD-CYN-0020 U-7): 配布物由来の相対参照を実行時に作り直す。

    姉妹系統 (単体配布版) の配布用 demo.db を作る道具 (tools/build_clean_demo_db.py) は
    files.path 等を「./dummy-corpus/...」へ相対化するが、識別子 files.id は梱包の場
    (mktemp の一時ステージ) の絶対パス由来のまま残る。走査 (_do_scan) の照合鍵は
    id = md5(source_id|NFC(絶対パス))[:16] だけなので、受け取り手の最初の再スキャンで
    同じ資料が新規行として二重登録され、旧行に「⚠️ 取り込みフォルダに実体が
    見つかりません」が付いていた (DD-CYN-0020 U-7 陰性対照で実測)。展開先の絶対パスは
    梱包時に知り得ないため、道具側では識別子を作り直せない (不成立を実証済み)。
    よって絶対パスが確定する唯一の時点 = 受け取り手の起動時に、ここで作り直す。

    berth の道具は現状パスを相対化しないため、この系統では対象 0 件で素通りする
    (guard)。道具が姉妹系統と同型に追随したときに同じ機構で受けるための同型実装。

    規則は走査側と同一 (走査側の規則は変えない):
      絶対化 = os.path.abspath(os.path.expanduser(パス))   (server.py _do_scan と同じ)
      識別子 = md5(source_id + "|" + NFC(絶対パス))[:16]    (server.py _stable_fid と同じ)
    対象は path が './' 始まりで、絶対化した先に実体が在る行だけ。実体の無い相対参照は
    触らない (走査が従来どおり missing を付ける)。冪等: 2回目以降は対象 0 件で素通り。

    追随 (呼び出し側が同一トランザクションとして commit/rollback する):
      - collection_files.file_id (files.id への FK。defer_foreign_keys で親子同時付け替え)
      - document_lineage.file_id / source_path
      - file_hashes.file_path (パス鍵のため path のみ。chunk_ids は削除用の不透明
        ハンドルで files.id と突き合わせないためそのまま)
      - chunks / Chroma の物理 id 文字列には旧 file_id が焼き込まれているが、読み手は
        id 文字列から自己完結で親を導くだけ (rag.py の rsplit("#c",1)) で files.id と
        照合しないため追随不要 (U-7 で実測)。次回 Publish は file_hashes のパス照合で
        「変更なし」となり、ベクトルの作り直しも起きない。
    未修正版で既に二重化した DB (絶対パス由来の新行が居る) は、相対行の参照を新行へ
    寄せて相対行を除き、二重登録を自己修復する。
    """
    import hashlib as _hl

    rows = conn.execute(
        "SELECT id, source_id, path FROM files WHERE path LIKE './%'"
    ).fetchall()
    if not rows:
        return 0
    # 親キー (files.id) と子 (collection_files.file_id) を同一トランザクション内で
    # 付け替えるあいだ、外部キー検査を commit 時まで猶予する (接続は FK ON のため)。
    # 注意: defer_foreign_keys は「トランザクションの終わり」ごとに OFF へ戻る。
    # autocommit 状態では、ループ内の SELECT (読み取りトランザクション) の終了でも
    # OFF へ戻ってしまい UPDATE が即時 FK 違反になる (実測 IntegrityError)。
    # 先に明示 BEGIN で1つの書き込みトランザクションを開き、その中で PRAGMA を
    # 発行する。commit / rollback は呼び出し側が行う。
    try:
        conn.execute("BEGIN")
    except sqlite3.OperationalError:
        pass  # 既にトランザクション内 (呼び出し側で DML 済み) ならそのまま乗る
    conn.execute("PRAGMA defer_foreign_keys = ON")
    n = 0
    for r in rows:
        old_id, source_id, old_path = r["id"], r["source_id"], r["path"]
        new_path = os.path.abspath(os.path.expanduser(old_path))
        if not os.path.exists(new_path):
            continue
        new_id = _hl.md5(
            f"{source_id}|{_nfc_path(new_path)}".encode(), usedforsecurity=False
        ).hexdigest()[:16]
        if new_id == old_id:
            conn.execute("UPDATE files SET path = ? WHERE id = ?", (new_path, old_id))
            n += 1
            continue
        if conn.execute("SELECT 1 FROM files WHERE id = ?", (new_id,)).fetchone():
            # 未修正版の再スキャンで既に二重化した DB: 参照を新行へ寄せ、相対行を除く
            conn.execute(
                "INSERT OR IGNORE INTO collection_files (collection_id, file_id) "
                "SELECT collection_id, ? FROM collection_files WHERE file_id = ?",
                (new_id, old_id),
            )
            conn.execute("DELETE FROM collection_files WHERE file_id = ?", (old_id,))
            conn.execute("DELETE FROM files WHERE id = ?", (old_id,))
        else:
            conn.execute(
                "UPDATE files SET id = ?, path = ?, missing = 0 WHERE id = ?",
                (new_id, new_path, old_id),
            )
            conn.execute(
                "UPDATE OR IGNORE collection_files SET file_id = ? WHERE file_id = ?",
                (new_id, old_id),
            )
            conn.execute("DELETE FROM collection_files WHERE file_id = ?", (old_id,))
        try:
            conn.execute(
                "UPDATE document_lineage SET file_id = ? WHERE file_id = ?",
                (new_id, old_id),
            )
            conn.execute(
                "UPDATE document_lineage SET source_path = ? WHERE source_path = ?",
                (new_path, old_path),
            )
        except Exception:
            pass  # lineage 表の無い旧スキーマでも起動を止めない
        try:
            conn.execute(
                "UPDATE OR IGNORE file_hashes SET file_path = ? WHERE file_path = ?",
                (new_path, old_path),
            )
            conn.execute("DELETE FROM file_hashes WHERE file_path = ?", (old_path,))
        except Exception:
            pass
        n += 1
    return n


def init_db(demo: bool = False):
    # Phase 3: Postgres backend は consolidated DDL でスキーマを冪等作成し、データは importer
    # （tools/pg_import_from_sqlite.py / seed 経路）が用意する。SQLite 専用の migrate_db /
    # numbered migrations / seed は方言非互換のため Postgres では走らせない。
    try:
        from core.config import CYNOVELA_CONFIG as _dtc_init

        _b = ((_dtc_init.get("database") or {}).get("backend") or "sqlite").lower()
    except Exception:
        _b = "sqlite"
    if _b in ("postgres", "postgresql", "pgvector"):
        try:
            from core.relational_pg import apply_consolidated_ddl

            apply_consolidated_ddl()
        except Exception as _pe:
            import logging as _lg_pg

            _lg_pg.getLogger("cynovela.db").warning("Postgres consolidated DDL 適用に失敗 (継続): %s", _pe)
        return

    conn = get_db()
    try:
        # Create all tables（migrate_dbに集約）
        migrate_db(conn)
        # FIX-038: migrations/_runner.apply_all を production 経路に配線。
        # FIX-039 (0002 冪等化) + FIX-040 (0001 動的列対応) 完了後の有効化。
        try:
            from migrations._runner import apply_all as _apply_all

            _apply_all(conn)
        except Exception as _mig_e:
            import logging as _logging_for_mig

            _logging_for_mig.getLogger("cynovela.db").warning("migrations apply_all 失敗 (init_db 起動継続): %s", _mig_e)

        # Always insert seed data (skip if already exists).
        # 個人名は使わず役割名 (Admin / Viewer) を使う方針。
        # 既存ユーザーの個人名は下の UPDATE で role-based 名に上書きする。
        users = [
            ("user-admin", "Admin", "admin", "👨‍💼"),
            ("user-scientist", "Viewer", "viewer", "🔬"),
        ]
        for u in users:
            conn.execute(
                "INSERT OR IGNORE INTO users (id, name, role, avatar) VALUES (?, ?, ?, ?)",
                u,
            )

        # role-based display_name を全 demo ユーザーに idempotent に適用する。
        # 既存環境では「田中 誠」等の個人名が seed されている可能性があるので、
        # name と display_name の両方を更新する。
        _now = _dt.now().isoformat(timespec="seconds")
        _role_map = {
            "user-admin": "Admin",
            "user-scientist": "Viewer",
        }
        for _uid, _dn in _role_map.items():
            conn.execute(
                "UPDATE users SET name = ?, display_name = ?, updated_at = ? WHERE id = ?",
                (_dn, _dn, _now, _uid),
            )

        # 認証情報指定 (Stage-2G-1, 2026-05-16): user-admin の username/password 初期 seed。
        # 起動毎リセットを廃止: password_hash が空 or username が "cynovela" 以外なら初回 seed として
        # 値を設定するが、既に admin が password を変更している場合は触らない。
        _existing_admin = conn.execute("SELECT username, password_hash FROM users WHERE id = ?", ("user-admin",)).fetchone()
        if _existing_admin is not None:
            _need_seed = not _existing_admin["password_hash"] or not _existing_admin["username"]
            if _need_seed:
                # FIX-021: 初期 admin パスワードを env 変数化 (本番運用で固定 "cynovela" 露出回避)。
                # fixall-B4 20260602: 供給優先順位を env 非依存化し、ランダム生成時は平文を実際に出力する。
                #   1) env CYNOVELA_ADMIN_INITIAL_PASSWORD            (従来互換・残すが必須にしない)
                #   2) cynovela.yaml の auth.admin_initial_password   (env を使わず yaml で初期 PW を指定可能に)
                #   3) いずれも無ければ secrets.token_urlsafe(16) でランダム生成し、平文を起動ログ + 標準出力に
                #      1回だけ明示する (旧コードはコメントだけで出力が無く、env 未設定だと初回 login 不能だった)。
                import os as _os
                import secrets as _secrets
                import logging as _logging

                _admin_password = _os.environ.get("CYNOVELA_ADMIN_INITIAL_PASSWORD")
                if not _admin_password:
                    # env 未指定なら cynovela.yaml の auth.admin_initial_password を試す (env 非依存経路)
                    try:
                        from core.config import get_yaml_config as _gyc

                        _admin_password = ((_gyc().get("auth") or {}).get("admin_initial_password")) or None
                    except Exception:
                        _admin_password = None
                # sec4 v4.1 項目②: ユーザー名も env 上書き可能に。既定値は "cynovela" のまま (テスト互換)。
                # 既存 user-admin 行 (password_hash と username が共に値あり) は _need_seed=False で
                # スキップされるので、本ブロックは「初回シード時のみ」発火し既存値を破壊しない。
                _admin_username = _os.environ.get("CYNOVELA_ADMIN_USERNAME") or "cynovela"
                # Batch-B S1-1: env/yaml で明示指定された場合のみ must_change_password=0。
                # ランダム生成時は 1 (管理者は初回ログイン後に必ず変更する必要がある)。
                if _admin_password:
                    _must_change = 0
                else:
                    _admin_password = _secrets.token_urlsafe(16)
                    _must_change = 1
                    _seed_msg = (
                        "[Cynovela] 初期 admin パスワードを自動生成しました "
                        "(env CYNOVELA_ADMIN_INITIAL_PASSWORD / cynovela.yaml auth.admin_initial_password とも未指定)。\n"
                        f"          username={_admin_username}  password={_admin_password}\n"
                        "          初回ログイン後に必ず変更してください。"
                    )
                    # sokessan-fix-a11-20260711: 平文パスワードは揮発的なコンソール出力(print)のみに残し、
                    # 永続する logger (store/logs/server.log) には password を書かない。
                    # ログには「自動生成した事実 + username」だけを残して監査性は維持する。
                    _seed_msg_log = (
                        "[Cynovela] 初期 admin パスワードを自動生成しました "
                        f"(username={_admin_username})。パスワードは初回起動時のコンソール出力のみに表示します "
                        "(セキュリティのためログファイルには記録しません)。初回ログイン後に必ず変更してください。"
                    )
                    _logging.getLogger("cynovela.db").warning(_seed_msg_log)
                    print(_seed_msg, flush=True)
                _admin_hash = hash_password(_admin_password)
                conn.execute(
                    "UPDATE users SET username = ?, password_hash = ?, must_change_password = ?, updated_at = ? WHERE id = ?",
                    (_admin_username, _admin_hash, _must_change, _now, "user-admin"),
                )

        # llm-endpoint-container-default-20260627: コンテナ形態では localhost:1234 がコンテナ自身を指し
        #   ホストの LM Studio に届かないため、種値もコンテナ対応の既定 (host.containers.internal) にする。
        #   値は core.llm.default_llm_endpoint() に単一定義 (スタンドアロンは従来どおり localhost)。
        #   遅延 import で循環 import (core.llm → db) を回避。
        from core.llm import default_llm_endpoint as _default_llm_endpoint
        settings = [
            ("llm_endpoint", _default_llm_endpoint()),
            ("llm_model", "auto"),
            ("embedding_model", "default"),
        ]
        for s in settings:
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", s)

        policies = [
            (
                "pol-pii",
                "PII保護ポリシー",
                '[{"classifier":"PII","action":"mask"},{"classifier":"Financial","action":"exclude_from_rag"}]',
                "active",
            ),
            (
                "pol-strict",
                "厳格管理ポリシー",
                '[{"classifier":"PII","action":"mask"},{"classifier":"Financial","action":"exclude_from_rag"},{"classifier":"HR","action":"exclude_from_rag"}]',
                "active",
            ),
            (
                "pol-log",
                "ログのみポリシー",
                '[{"classifier":"PII","action":"log_only"},{"classifier":"Financial","action":"log_only"}]',
                "active",
            ),
        ]
        for p in policies:
            conn.execute(
                "INSERT OR IGNORE INTO guardrail_policies (id, name, rules, state) VALUES (?, ?, ?, ?)",
                p,
            )

        # v3.0.1 bug2: viewer (user-scientist) の username/password 初期 seed。
        # seed する経路がどこにも無く、migrate_db の backfill がランダム hash を
        # 入れるため起動直後の viewer ログインが不能だった。admin seed (Stage-2G-1)
        # と同じ「username か password_hash が未設定の初回のみ」条件で seed し、
        # 既に値がある行 (パスワード変更済み等) は触らない。
        #
        # DD-CYN-0115 M-3 (姉妹系統の DD-CYN-0070 N-4 と同じ振る舞い): このブロックは従来
        # `if demo:` の中に在り、引数なし (本番) では閲覧者の資格情報が作られず、
        # 「行は作られるが値が空」のまま案内の値で入れなかった。配布仕様書 §5-4 は
        # 利用者を管理者と閲覧者の2つと定め、受け入れ項は「閲覧者で入れる」を合格条件と
        # する。∴ demo 分岐の外へ移し、本番でも同じ供給順序で seed する。発火条件は
        # 従来のままなので、同梱 demo.db (build_clean_demo_db.py が seed 済み) と
        # 既存の行には何もしない。
        #
        # pw-out-of-code-20260729 (C-B9): 閲覧者の初期パスワードもコードに平文で
        # 置かない。admin seed と同じ供給順序にする。
        #   1) cynovela.yaml の auth.viewer_initial_password (指定した場合のみその値)
        #   2) 未指定なら secrets.token_urlsafe(12) でその場に生成し、平文は
        #      初回起動時のコンソール出力 (print) に 1 回だけ出す。
        # 永続する logger (store/logs/server.log) には平文を書かない
        # (sokessan-fix-a11-20260711 と同じ扱い)。
        _viewer_row = conn.execute(
            "SELECT username, password_hash FROM users WHERE id = ?", ("user-scientist",)
        ).fetchone()
        if _viewer_row is not None and (
            not _viewer_row["username"] or not _viewer_row["password_hash"]
        ):
            import secrets as _secrets
            import logging as _logging

            _viewer_username = "demo"
            try:
                from core.config import get_yaml_config as _gyc

                _viewer_password = ((_gyc().get("auth") or {}).get("viewer_initial_password")) or None
            except Exception:
                _viewer_password = None
            if not _viewer_password:
                _viewer_password = _secrets.token_urlsafe(12)
                print(
                    "[Cynovela] 閲覧者の初期パスワードを自動生成しました "
                    "(cynovela.yaml auth.viewer_initial_password 未指定)。\n"
                    f"          username={_viewer_username}  password={_viewer_password}\n"
                    "          この表示は初回起動時の 1 回だけです。",
                    flush=True,
                )
                _logging.getLogger("cynovela.db").warning(
                    "[Cynovela] 閲覧者の初期パスワードを自動生成しました "
                    f"(username={_viewer_username})。パスワードは初回起動時のコンソール出力のみに"
                    "表示します (セキュリティのためログファイルには記録しません)。"
                )
            conn.execute(
                "UPDATE users SET username = ?, password_hash = ?, must_change_password = 0, "
                "updated_at = ? WHERE id = ?",
                (_viewer_username, hash_password(_viewer_password), _now, "user-scientist"),
            )

        if demo:
            # Demo sources (修正#B: 人事法務フォルダ ./sample_data/hr は0件で混乱の元なので削除)
            demo_sources = [
                ("src-tech", "技術文書フォルダ", "./sample_data/technical", "idle", 0),
                ("src-shared", "社内共有フォルダ", "./sample_data", "idle", 0),
            ]
            for s in demo_sources:
                conn.execute(
                    "INSERT OR IGNORE INTO sources (id, name, path, status, file_count) VALUES (?, ?, ?, ?, ?)",
                    s,
                )
            # 修正#B: 過去シードで残った可能性のある src-hr (人事法務フォルダ) を最初の起動で除去
            try:
                conn.execute("DELETE FROM workspace_sources WHERE source_id = 'src-hr'")
                conn.execute("DELETE FROM files WHERE source_id = 'src-hr'")
                conn.execute("DELETE FROM sources WHERE id = 'src-hr'")
            except Exception:
                pass

            # Demo workspaces
            demo_workspaces = [
                ("ws-sales", "営業＆財務"),
                ("ws-tech", "技術文書"),
                ("ws-hr", "人事・法務"),
            ]
            for w in demo_workspaces:
                conn.execute("INSERT OR IGNORE INTO workspaces (id, name) VALUES (?, ?)", w)

            # WS-Source links (修正#B: src-hr は削除済みなので ws-hr → src-shared に移行)
            ws_source_links = [
                ("ws-sales", "src-shared"),
                ("ws-tech", "src-tech"),
                ("ws-hr", "src-shared"),
            ]
            for link in ws_source_links:
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_sources (workspace_id, source_id) VALUES (?, ?)",
                    link,
                )

            # WS-Policy links（P2-D: 中間テーブルに移行。guardrail_policy_id列は旧データ互換用）
            ws_policy_links = [
                ("ws-sales", "pol-pii"),
                ("ws-hr", "pol-strict"),
                ("ws-tech", "pol-log"),
            ]
            for ws_id, pid in ws_policy_links:
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_policies (workspace_id, policy_id) VALUES (?, ?)",
                    (ws_id, pid),
                )

            # WS-User links
            # All WS get admin
            for ws_id in ["ws-sales", "ws-tech", "ws-hr"]:
                conn.execute(
                    "INSERT OR IGNORE INTO workspace_users (workspace_id, user_id) VALUES (?, ?)",
                    (ws_id, "user-admin"),
                )
            # ws-tech gets scientist
            conn.execute(
                "INSERT OR IGNORE INTO workspace_users (workspace_id, user_id) VALUES (?, ?)",
                ("ws-tech", "user-scientist"),
            )

        conn.commit()

        # dist-file-id-rebase-20260802 (DD-CYN-0020 U-7): 配布物由来の相対参照
        # (files.path が './' 始まり) を実行時の絶対パスへ付け替え、識別子を走査側と
        # 同じ規則で作り直す (デモ・本番共通。対象行が無ければ 0 件で素通り = 冪等。
        # berth の道具は現状相対化しないため通常 0 件)。
        # 上のシード類とは分けて自前のトランザクションで確定し、失敗時は rollback
        # だけして起動は続ける。
        try:
            _n_rebased = _rebase_relative_files(conn)
            conn.commit()
            if _n_rebased:
                import logging as _logging_for_rebase

                _logging_for_rebase.getLogger("cynovela.db").info(
                    "配布物由来の相対参照 %d 件を絶対パスへ付け替え、識別子を作り直した",
                    _n_rebased,
                )
        except Exception as _rb_e:
            conn.rollback()
            import logging as _logging_for_rebase

            _logging_for_rebase.getLogger("cynovela.db").warning(
                "相対参照の付け替えに失敗 (起動継続): %s", _rb_e
            )
    finally:
        conn.close()
