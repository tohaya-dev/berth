from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn
import sys
import os
import json
import re
import asyncio
import logging
import mimetypes
import threading
import subprocess
import unicodedata
from contextlib import asynccontextmanager
from datetime import datetime

logger = logging.getLogger("cynovela")
logging.getLogger("cynovela").setLevel(logging.INFO)

# FIX-047: Handler 登録 (dictConfig 化)。標準ライブラリのみで FileHandler + StreamHandler を
# 配線、Formatter でタイムスタンプ + ロガー名 + level + request_id (FIX-048 後で extra 注入)
# を出力する。cynovela.yaml:140-142 の level/log_file が未配線な場合のフォールバック。
try:
    import logging.config as _logging_config
    import pathlib as _pathlib

    # 状態は store/ 配下に集約 (設計: ホームには状態を置かない)。CYNOVELA_LOG_DIR は
    # 後段 (paths: 解決ブロック) で store/logs に setdefault されるが、本ロギング初期化は
    # それより前に走るため、既定値もここで store/logs を直接解決する。env 明示時はそれを優先。
    _log_dir = _pathlib.Path(os.path.expanduser(os.environ.get(
        "CYNOVELA_LOG_DIR",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "store", "logs"),
    )))
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / "server.log"
    _logging_config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": ("%(asctime)s %(levelname)s [%(name)s] " "request_id=%(request_id)s %(message)s"),
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "filters": {
                "default_request_id": {
                    "()": "logging.Filter",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": "INFO",
                    "formatter": "default",
                    "filters": [],
                },
                "file": {
                    "class": "logging.FileHandler",
                    "level": "INFO",
                    "formatter": "default",
                    "filename": str(_log_file),
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "cynovela": {
                    "level": "INFO",
                    "handlers": ["console", "file"],
                    "propagate": False,
                },
            },
        }
    )

    # request_id は FIX-048 middleware が ContextVar 経由で extra 注入する。
    # 未配線時のフォールバックとして "-" を埋め込む LogFilter を全 handler に追加。
    class _RequestIDDefaultFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if not hasattr(record, "request_id"):
                record.request_id = "-"
            return True

    _rid_filter = _RequestIDDefaultFilter()
    for _h in logging.getLogger("cynovela").handlers:
        _h.addFilter(_rid_filter)
except Exception as _log_setup_err:
    # 起動初期の logging 設定失敗は致命でないので継続 (既存 lastResort へフォールバック)
    print(f"[FIX-047] logging dictConfig setup failed: {_log_setup_err}")

# Fix SSL cert path if needed (macOS homebrew openssl issue)
if os.environ.get("SSL_CERT_FILE") and not os.path.exists(os.environ["SSL_CERT_FILE"]):
    for candidate in ["/etc/ssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"]:
        if os.path.exists(candidate):
            os.environ["SSL_CERT_FILE"] = candidate
            break


# Fix-7 (Stage R4-1): --demo / --mock 起動時は本番 DB / Chroma と分離する。
#         db.py / rag.py の import 前に環境変数を設定する必要があるため、
#         argparse の `parse_known_args` で早期に sys.argv を解釈する正規経路に置換。
#         （旧実装: `"--demo" in sys.argv` の文字列マッチ hack。Phase 3 Recon Agent K §1-3 で脆さ指摘済み）
def _early_parse_demo() -> bool:
    # v3.5.0 Stage1-B: --mock 起動コード経路を完全撤去 (--demo は保持)。
    import argparse as _argparse

    p = _argparse.ArgumentParser(add_help=False)
    p.add_argument("--demo", action="store_true")
    args, _ = p.parse_known_args()
    return bool(args.demo)


_early_demo = _early_parse_demo()
# release/v1.0.0-alpha (2026-05-24): パス解決を cynovela.yaml の paths: セクションに一本化。
# 起動モード (フラグ無し / --demo / --mock) を問わず、env CYNOVELA_DB / CYNOVELA_CHROMA が
# 未設定なら cynovela.yaml の paths から ./store 配下の demo DB / Chroma を既定値にする。
# env で export 上書きされている場合は setdefault が no-op になる (既存値優先)。
try:
    import yaml as _yaml_for_paths
    _yaml_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cynovela.yaml")
    with open(_yaml_path) as _f:
        _paths_cfg = _yaml_for_paths.safe_load(_f).get("paths", {})
    _data_dir_root = os.path.realpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        _paths_cfg.get("data_dir", "./store")
    ))
    _demo_db_rel  = _paths_cfg.get("db", {}).get("demo" if _early_demo else "clean", "db/demo.db" if _early_demo else "db/cynovela.db")
    # DD-CYN-0116 X-6 U-13: 従来ここだけ 'demo' を固定で引いており、本番起動でも
    #   デモ用の索引を指していた (DB は demo/clean で分けているのに索引は共有)。
    #   yaml の paths.vector.default は誰にも読まれない死んだ設定になっていた。
    #   DB 側 (_demo_db_rel) と同じ選び方に揃える。
    _chroma_rel   = _paths_cfg.get("vector", {}).get(
        "demo" if _early_demo else "default",
        "vector/demo/chroma" if _early_demo else "vector/default/chroma",
    )
    _backup_rel   = _paths_cfg.get("backups", "backups")
    _log_rel      = _paths_cfg.get("logs",    "logs")
    _models_rel   = _paths_cfg.get("models",  "models")
except Exception:
    _data_dir_root = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "store"))
    _demo_db_rel   = "db/demo.db" if _early_demo else "db/cynovela.db"
    # fix-chromapath-20260525: PersistentClient は /chroma 込みのディレクトリを期待する。
    # yaml 読み込み成功時 (上の try ブロック) と整合させる。
    # U-13: 例外側も DB の選び方に揃える
    _chroma_rel    = "vector/demo/chroma" if _early_demo else "vector/default/chroma"
    _backup_rel    = "backups"
    _log_rel       = "logs"
    _models_rel    = "models"
os.environ.setdefault("CYNOVELA_DB",         os.path.join(_data_dir_root, _demo_db_rel))
os.environ.setdefault("CYNOVELA_CHROMA",     os.path.join(_data_dir_root, _chroma_rel))
os.environ.setdefault("CYNOVELA_BACKUP_DIR", os.path.join(_data_dir_root, _backup_rel))
os.environ.setdefault("CYNOVELA_LOG_DIR",    os.path.join(_data_dir_root, _log_rel))
os.environ.setdefault("CYNOVELA_DATA_DIR",   _data_dir_root)
try:
    for _d in [os.path.dirname(os.environ["CYNOVELA_DB"]),
               os.environ["CYNOVELA_CHROMA"],
               os.environ["CYNOVELA_BACKUP_DIR"],
               os.environ["CYNOVELA_LOG_DIR"]]:
        os.makedirs(_d, exist_ok=True)
except Exception:
    pass

from db import init_db, get_db, new_id, hash_password, verify_password
import secrets
from rag import (
    extract_text,
    publish_collection,
    publish_collection_iter,
    rag_retrieve,
    call_llm,
    request_publish_stop,
    DEFAULT_SYSTEM_PROMPT,
    GENERAL_KNOWLEDGE_SYSTEM_PROMPT,
    apply_role_prefix,
    set_embedding_provider,
    get_embedding_provider_current,
    SUPPORTED_EXTENSIONS,
)
from providers.embedding import get_embedding_provider
from providers.vector_store import get_vector_store_provider, ChromaDBVectorStore, QdrantVectorStore
from providers.classifier import get_classifier_provider, RuleBasedClassifier, APIClassifier
from providers.reranker import (
    get_reranker_provider,
)
from rag import set_reranker_provider, get_reranker_provider_current
from providers.registry import ProviderRegistry

# Phase 2 Step 3-5: Module-level Providers
try:
    from core.config import CYNOVELA_CONFIG as _DTC3

    _vector_store = get_vector_store_provider(_DTC3)
    _classifier = get_classifier_provider(_DTC3)
    _registry = ProviderRegistry(_DTC3)
except Exception:
    _vector_store = ChromaDBVectorStore()
    _classifier = RuleBasedClassifier()
    _registry = ProviderRegistry({})
from llm_adapter import get_llm_adapter, MockAdapter, OpenAICompatibleAdapter
from pipeline_types import PipelineResult, RetrievalResult
from classifier import classify_file, classify_metadata
from core.config import is_feature_enabled
from guardrail import apply_guardrail
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_counts_from_db

import state as _state
from core.auth import _require_admin, get_user_from_token, _audit_auth_failure
from core.errors import api_error
from core.audit import _log_audit, log_admin_change, _AUDIT_CATEGORY_MAP, _audit_category
from core.constants import _ARCHIVABLE, COMPARE_MODEL_PRESETS
from core.roles import ROLE_DEMO_WS_NAME, VALID_ROLES


@asynccontextmanager
async def lifespan(app_instance):
    """アプリ起動・終了時の処理。@app.on_event("startup") の代替。

    モジュール下方で定義される _log_cleanup_loop / _startup_reset_residual_publish_jobs /
    _startup_rebuild_bm25 を参照するが、Python は関数 body 内の名前を呼び出し時に解決するため
    forward reference として安全。
    """
    # ── 起動時処理 ──────────────────────────────────────
    try:
        asyncio.create_task(_log_cleanup_loop())
    except Exception as _e:
        logger.warning(f"log cleanup task spawn failed: {_e}")
    try:
        await _startup_reset_residual_publish_jobs()
    except Exception as _e:
        logger.warning(f"residual publish job reset failed at lifespan: {_e}")
    # Batch-B S1-3: 期限切れリフレッシュトークンを削除
    try:
        _startup_db = get_db()
        try:
            deleted = _startup_db.execute(
                "DELETE FROM refresh_tokens WHERE expires_at < datetime('now')"
            ).rowcount
            _startup_db.commit()
        finally:
            _startup_db.close()
        if deleted:
            logger.info(f"Startup: removed {deleted} expired refresh token(s)")
    except Exception as _e:
        logger.warning(f"Startup: failed to cleanup refresh tokens: {_e}")
    try:
        await _startup_rebuild_bm25()
    except Exception as _e:
        logger.warning(f"BM25 startup rebuild failed at lifespan: {_e}")
    # harden B-1: unified(pgvector) の孤児ベクター(関係層に親なし)を起動時に再整合
    try:
        await _startup_reconcile_pgvector_orphans()
    except Exception as _e:
        logger.warning(f"pgvector orphan reconcile failed at lifespan: {_e}")
    # DD-CYN-0116 Z-3: 関係層に本文があるのに索引が空なら作り直す (種データからの復元を
    # 配備スクリプト頼みにしない)。
    try:
        await _startup_rebuild_vectors_from_relational()
    except Exception as _e:
        logger.warning(f"vector rebuild from relational failed at lifespan: {_e}")
    # DD-CYN-0116 F-5: 取り込み元の正本を Pod ごとのファイルから共有の DB へ一度だけ移す。
    # 以後はどのレプリカが答えても同じ一覧になる。
    try:
        from routers.sources import migrate_ingest_roots_to_db as _mir

        _n = _mir()
        if _n >= 0:
            logger.info(f"[Cynovela] 取り込み元を共有の記録へ移しました ({_n} 件)")
    except Exception as _e:
        logger.warning(f"ingest roots migration failed at lifespan: {_e}")
    # DD-CYN-0116 X-1 / G-9: 索引を作った埋め込みと、いまの埋め込みの突き合わせを
    # 起動時に一度行い、食い違いを起動ログへ出す (画面は /api/settings/embedding 経由)。
    try:
        import rag as _rag_ident

        _ident0 = _rag_ident.check_embedding_identity()
        if _ident0.get("match") is False:
            logger.warning(f"[Cynovela] 埋め込み識別の食い違い: {_ident0.get('message')}")
        elif _ident0.get("match") is None:
            logger.info(f"[Cynovela] 埋め込み識別: {_ident0.get('message')}")
    except Exception as _e:
        logger.warning(f"embedding identity check failed at lifespan: {_e}")

    yield

    # ── 終了時処理 ───────────────────
    # PORTABILITY FIX 20260527 Stage2 M11: AuditLogListener executor を確実に flush + shutdown
    try:
        from services.listeners import AuditLogListener
        if AuditLogListener._executor is not None:
            AuditLogListener._executor.shutdown(wait=True)
            logger.info("[Cynovela] AuditLogListener executor shutdown 完了")
    except Exception as _e:
        logger.warning(f"AuditLogListener executor shutdown failed: {_e}")


from core.version import APP_VERSION as _APP_VERSION  # DD-CYN-0115 R-5: 正本=VERSION

app = FastAPI(title="Cynovela", version=_APP_VERSION, lifespan=lifespan)

# STEP 5: SlowAPI レートリミット
# 既定: 200 req/min/IP, /api/chat は 30 req/min/IP
# 環境変数 CYNOVELA_DISABLE_RATE_LIMIT=1 で無効化 (テスト/CI 用)
if os.environ.get("CYNOVELA_DISABLE_RATE_LIMIT") in ("1", "true", "yes"):
    logger.info("SlowAPI レートリミット 無効化 (CYNOVELA_DISABLE_RATE_LIMIT=1)")
    limiter = None
else:
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.util import get_remote_address
        from slowapi.errors import RateLimitExceeded

        limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])
        app.state.limiter = limiter
        # SlowAPI の handler シグネチャは ExceptionHandler protocol と非互換 (RateLimitExceeded 専用)
        # FastAPI は runtime ダックタイプで受け入れるため抑制
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]
    except Exception as _e:
        logger.warning(f"SlowAPI 初期化失敗 (rate limit 無効): {_e}")
        limiter = None


# STEP 5: /api/chat はレートリミット 30 req/min/IP
# SlowAPI デコレータは @app.post の「下」に配置すること (FastAPI ルーター登録後にラップ)
# _chat_rate_limit は routers/chat.py または core/llm.py に移動済み


# PORTABILITY FIX 20260527 P1: CORS origins を cynovela.yaml の server.cors_origins から読む
try:
    from core.config import load_yaml_config as _load_yaml_for_cors
    _cors_yaml = (_load_yaml_for_cors().get("server") or {}).get("cors_origins")
    _cors_origins = _cors_yaml if isinstance(_cors_yaml, list) and _cors_yaml else \
        ["http://localhost:8765", "http://127.0.0.1:8765"]
except Exception:
    _cors_origins = ["http://localhost:8765", "http://127.0.0.1:8765"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 不正な JSON ボディを 400 として返す (デフォルトでは ValueError が伝播し 500 になる)。
@app.exception_handler(json.JSONDecodeError)
async def _json_decode_error_handler(request: Request, exc: json.JSONDecodeError):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=400,
        content={"error": "INVALID_JSON", "message": f"Request body is not valid JSON: {exc.msg}"},
    )


# ─── P1 §3: 構造化エラーレスポンスヘルパー ───
# api_error は core/errors.py に移動済み。後方互換のため server からも参照可能。


# ─── P1 §8-1: Prompt Injection 検出 (Guardrail デモ用) ───
# シンプルな正規表現ベースの検出。誤検出を許容して教育目的に振る。
import re as _re_inj
# INJECTION_PATTERNS は routers/chat.py または core/llm.py に移動済み


# detect_prompt_injection は routers/chat.py または core/llm.py に移動済み

# PHASE B-1: IP アローリストミドルウェア (起動時に _allowed_subnets が設定されたら有効化)
# 127.0.0.1 と localhost は常に許可。--allow-tailscale / --allow-subnet で追加。
_allowed_subnets: list = []  # ipaddress.IPv4Network のリスト (起動時に設定)


# FIX-050: /metrics エンドポイント配線 (prometheus_fastapi_instrumentator)。
# 依存未 install 環境では本配線をスキップ (start fail を回避、metric 取得は不可になるが起動継続)。
try:
    from prometheus_fastapi_instrumentator import Instrumentator as _Instrumentator

    _Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    logger.info("[FIX-050] /metrics endpoint configured via prometheus_fastapi_instrumentator")
except ImportError:
    logger.info("[FIX-050] prometheus_fastapi_instrumentator 未 install、/metrics 配線スキップ")
except Exception as _metrics_err:
    logger.exception(f"[FIX-050] /metrics 配線失敗: {_metrics_err}")


# FIX-048: request_id middleware (X-Request-ID 全エンドポイント波及)。
# starlette BaseHTTPMiddleware + contextvars で extra 自動注入する。
# logger.* 呼出時に %(request_id)s が dictConfig (FIX-047) で埋め込まれる。
import contextvars as _contextvars
import uuid as _uuid_for_rid

_request_id_var: _contextvars.ContextVar[str] = _contextvars.ContextVar("request_id", default="-")


class _RequestIDLogFilter(logging.Filter):
    """logger.* 呼出時に ContextVar の request_id を LogRecord.extra として注入。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = _request_id_var.get()
        except Exception:
            record.request_id = "-"
        return True


for _h in logging.getLogger("cynovela").handlers:
    _h.addFilter(_RequestIDLogFilter())


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    """X-Request-ID を全リクエストに付与し、ContextVar 経由で logger に extra 注入。"""
    rid = request.headers.get("X-Request-ID") or _uuid_for_rid.uuid4().hex[:16]
    _token = _request_id_var.set(rid)
    try:
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        _request_id_var.reset(_token)


@app.middleware("http")
async def _ip_allowlist_middleware(request: Request, call_next):
    """B-1: 設定された場合のみアローリストを適用。未設定なら全通過 (既存挙動互換)。"""
    if not _allowed_subnets:
        return await call_next(request)
    import ipaddress

    client_ip_str = (request.client.host if request.client else "127.0.0.1") or "127.0.0.1"
    try:
        client_ip = ipaddress.ip_address(client_ip_str)
    except ValueError:
        # IPv6 マッピングや異常値は通す (誤検知回避)
        return await call_next(request)
    # localhost は常に許可
    if client_ip.is_loopback:
        return await call_next(request)
    for net in _allowed_subnets:
        if client_ip in net:
            return await call_next(request)
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=403,
        content={"detail": f"Forbidden: client IP {client_ip_str} is not in allowed subnets"},
    )


def _detect_tailscale_ip() -> str | None:
    """tailscale ip -4 で検出。失敗時は None。"""
    try:
        import subprocess as _sp

        r = _sp.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            # 1 行目を採用 (デバイスに複数 IP がある場合は最初のもの)
            return r.stdout.splitlines()[0].strip()
    except Exception:
        pass
    return None


# SUPPORTED_EXTENSIONS は rag.py を single source of truth として import 済み
# (Stage-D #3: server.py の独自定義を削除し `from rag import SUPPORTED_EXTENSIONS` で委譲)


# ─── 実行時モード（--demo / --mock） ───
# _args は state.config に移行済み。_state.config を参照すること。

# Phase 2: cynovela.yaml の llm セクションを正にしてアダプターを初期化
try:
    from core.config import CYNOVELA_CONFIG as _DTC

    _llm_cfg = _DTC.get("llm", {})
    _adapter = get_llm_adapter(
        base_url=_llm_cfg.get("base_url", "http://localhost:1234"),
        provider=_llm_cfg.get("provider", "lmstudio"),
        model=_llm_cfg.get("model", ""),
        api_key=_llm_cfg.get("api_key", ""),
    )
except Exception:
    _adapter = get_llm_adapter("http://localhost:1234", mock=False)


# P1-2/P1-3: LLM 周辺ガード (CircuitBreaker + Semaphore)
# __main__ で初期化されるが、テスト等で server を import しただけでもエラーにならないよう
# モジュールロード時に最低限のフォールバックを用意する。
from providers.circuit_breaker import CircuitBreaker as _CB, CircuitBreakerOpenError
import asyncio as _asyncio_mod

_llm_circuit_breaker = _CB(
    service_name="LM Studio",
    failure_threshold=3,
    recovery_timeout=30.0,
    enabled=True,
)
_llm_semaphore = _asyncio_mod.Semaphore(3)


# _guarded_call_llm は routers/chat.py または core/llm.py に移動済み


# _get_llm_params_overrides は routers/chat.py または core/llm.py に移動済み


def _resolve_collection_chunking(col_id: str) -> tuple[int, int]:
    """GUI修正(2026-05-01) #6: コレクション単位の chunk_size / chunk_overlap を解決する。
    優先順位:
      1. collections テーブルの個別値 (NULL でなければ最優先)
      2. settings.chunking.chunk_size / chunk_overlap (グローバル上書き)
      3. config.rag.parent_child_enabled=True なら child_chunk_size/overlap (256/32)
      4. config.chunking.chunk_size / chunk_overlap (300/50)
      5. ハードコード既定値 500/50
    """
    from core.config import CYNOVELA_CONFIG as _CCFG

    _rag_cfg = _CCFG.get("rag") or {}
    _ck_cfg = _CCFG.get("chunking") or {}
    if _rag_cfg.get("parent_child_enabled", False):
        cs = int(_rag_cfg.get("child_chunk_size", 256))
        co = int(_rag_cfg.get("child_chunk_overlap", 32))
    else:
        cs = int(_ck_cfg.get("chunk_size", 500))
        co = int(_ck_cfg.get("chunk_overlap", 50))
    _global = _get_chunking_overrides()
    if "chunk_size" in _global:
        cs = int(_global["chunk_size"])
    if "chunk_overlap" in _global:
        co = int(_global["chunk_overlap"])
    conn = get_db()
    try:
        row = conn.execute("SELECT chunk_size, chunk_overlap FROM collections WHERE id = ?", (col_id,)).fetchone()
        if row is not None:
            if row["chunk_size"] is not None:
                cs = int(row["chunk_size"])
            if row["chunk_overlap"] is not None:
                co = int(row["chunk_overlap"])
    finally:
        conn.close()
    return cs, co


def _get_chunking_overrides() -> dict:
    """#06: settings DB から chunking.chunk_size / chunking.chunk_overlap を読む。"""
    conn = get_db()
    rows = {}
    try:
        for row in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('chunking.chunk_size', 'chunking.chunk_overlap')"
        ).fetchall():
            rows[row["key"]] = row["value"]
    finally:
        conn.close()
    out: dict = {}
    for k in ("chunking.chunk_size", "chunking.chunk_overlap"):
        v = (rows.get(k) or "").strip()
        if v.isdigit():
            out[k.split(".")[1]] = int(v)
    return out


# _current_adapter は core/llm.py の get_current_adapter に置き換え済み


# ─── Helpers ───


def row_to_dict(row):
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    return [dict(r) for r in rows]


def parse_policy_ids(value) -> list:
    """guardrail_policy_id列はJSON配列（複数適用）か単一ID（旧形式）/NULLを保持する。"""
    if not value:
        return []
    s = str(value).strip()
    if s.startswith("["):
        try:
            v = json.loads(s)
            return [x for x in v if x]
        except Exception:
            return []
    return [s]


def serialize_policy_ids(ids: list) -> str | None:
    ids = [i for i in (ids or []) if i]
    if not ids:
        return None
    return json.dumps(ids)


def compute_exclude_paths_for_collection(conn, col_id: str) -> set:
    """P2-D: Collectionに紐づくWSのGuardrailポリシーから `exclude_from_rag` 分類を集め、
    対象ファイルのパス集合を返す。ポリシーがなければ空集合。"""
    col = conn.execute("SELECT workspace_id FROM collections WHERE id = ?", (col_id,)).fetchone()
    if not col:
        return set()
    ws = conn.execute("SELECT guardrail_policy_id FROM workspaces WHERE id = ?", (col["workspace_id"],)).fetchone()
    if not ws:
        return set()
    pids = [
        r["policy_id"]
        for r in conn.execute(
            "SELECT policy_id FROM workspace_policies WHERE workspace_id = ?",
            (col["workspace_id"],),
        ).fetchall()
    ]
    if not pids:
        pids = parse_policy_ids(ws["guardrail_policy_id"])  # 後方互換
    if not pids:
        return set()
    exclude_classifiers: set = set()
    for pid in pids:
        pol = conn.execute("SELECT rules FROM guardrail_policies WHERE id = ?", (pid,)).fetchone()
        if not pol:
            continue
        try:
            rules = json.loads(pol["rules"])
        except Exception:
            continue
        for r in rules or []:
            if not isinstance(r, dict):
                continue
            if r.get("action") == "exclude_from_rag":
                cls = r.get("classifier")
                if cls:
                    exclude_classifiers.add(cls)
    if not exclude_classifiers:
        return set()
    # Collection配下のファイルcategories×exclude_classifiersを照合
    rows = conn.execute(
        """
        SELECT f.path, f.categories FROM files f
        JOIN collection_files cf ON f.id = cf.file_id
        WHERE cf.collection_id = ?
        """,
        (col_id,),
    ).fetchall()
    excluded: set = set()
    for r in rows:
        try:
            cats = json.loads(r["categories"] or "[]")
        except Exception:
            cats = []
        if any(c in exclude_classifiers for c in cats):
            excluded.add(r["path"])
    return excluded


# get_user_from_token / _audit_auth_failure / _require_admin は core/auth.py に移動済み（server.py 冒頭で import）
# _sessions (mutable global) も state.sessions に移動済み


# /api/health, /api/health/db, /api/health/vector, /api/health/guardrails は
# routers/health.py に移動済み


# /api/cost/estimate は routers/cost.py に移動済み


# ─── P3 §4 §5: Performance / Model / RAG quality stats ───


def _disk_usage_bytes(path: str) -> int:
    """ディレクトリ・ファイルサイズを再帰的に集計 (BFS, シンボリックリンクは無視)."""
    import os as _os

    if not _os.path.exists(path):
        return 0
    if _os.path.isfile(path):
        try:
            return _os.path.getsize(path)
        except Exception:
            return 0
    total = 0
    for root, dirs, files in _os.walk(path, followlinks=False):
        for f in files:
            try:
                total += _os.path.getsize(_os.path.join(root, f))
            except Exception:
                pass
    return total


# /api/stats/performance, /api/stats/model, /api/stats/rag-quality は routers/stats.py に移動済み


# ════════════════════════════════════════════════════════════
# P4 Block 1: Document Provenance (sha256 + version)
# ════════════════════════════════════════════════════════════
import hashlib as _hashlib_p4


def compute_sha256(filepath: str) -> str:
    """ファイルの SHA256 を 8KB 単位で計算する."""
    h = _hashlib_p4.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# PHASE 7: テスト互換用エイリアス (PHASE 12 cynovela_agent.py と命名統一)
_compute_sha256 = compute_sha256


def record_provenance(
    document_id: str,
    collection_id: str,
    filename: str,
    filepath: str,
    published_by: str = "unknown",
) -> dict:
    """Publish 時の Provenance を記録. 既存 current 行を is_current=0 に下げる.

    Returns: 追加したレコード dict
    """
    sha = compute_sha256(filepath)
    size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
    conn = get_db()
    try:
        # 既存 current を解除
        conn.execute(
            "UPDATE document_provenance SET is_current = 0 " "WHERE document_id = ? AND collection_id = ?",
            (document_id, collection_id),
        )
        row = conn.execute(
            "SELECT MAX(version) AS m FROM document_provenance " "WHERE document_id = ? AND collection_id = ?",
            (document_id, collection_id),
        ).fetchone()
        next_ver = int((row["m"] or 0)) + 1
        rec_id = new_id()
        now_iso = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO document_provenance
               (id, document_id, collection_id, filename, sha256, file_size,
                version, published_at, published_by, is_current)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (rec_id, document_id, collection_id, filename, sha, size, next_ver, now_iso, published_by),
        )
        conn.commit()
        return {
            "id": rec_id,
            "document_id": document_id,
            "collection_id": collection_id,
            "filename": filename,
            "sha256": sha,
            "file_size": size,
            "version": next_ver,
            "published_at": now_iso,
            "published_by": published_by,
            "is_current": 1,
        }
    finally:
        conn.close()


# /api/collections/{collection_id}/provenance は routers/collections.py に移動済み


# log_admin_change は core/audit.py に移動済み


# /api/admin/change-log は routers/admin.py に移動済み


# ════════════════════════════════════════════════════════════
# P4 Block 3: Log retention auto-cleanup
# ════════════════════════════════════════════════════════════
async def _log_cleanup_loop() -> None:
    """24 時間ごとに audit_logs / admin_change_log を retention 超過で削除."""
    while True:
        try:
            conn = get_db()
            try:
                row = conn.execute("SELECT value FROM settings WHERE key = 'log_retention_days'").fetchone()
                days = 90
                if row and (row["value"] or "").strip().isdigit():
                    days = max(7, min(int(row["value"]), 365))
                cutoff = f"-{days} days"
                deleted_audit = (
                    conn.execute(
                        "DELETE FROM audit_logs WHERE timestamp < datetime('now', ?)",
                        (cutoff,),
                    ).rowcount
                    or 0
                )
                deleted_admin = (
                    conn.execute(
                        "DELETE FROM admin_change_log WHERE timestamp < datetime('now', ?)",
                        (cutoff,),
                    ).rowcount
                    or 0
                )
                conn.commit()
                if deleted_audit + deleted_admin > 0:
                    logger.info(
                        f"log cleanup: removed audit={deleted_audit} " f"admin={deleted_admin} (retention {days} days)"
                    )
            finally:
                conn.close()
        except Exception as _e:
            logger.warning(f"log cleanup failed: {_e}")
        await asyncio.sleep(86400)


# lifespan に移行済み: _p4_startup_log_cleanup は lifespan で
# asyncio.create_task(_log_cleanup_loop()) を直接呼び出す形に置換した。


async def _startup_reset_residual_publish_jobs():
    """起動時に、サーバー異常終了で残った publishing 状態を回復する。

    - publish_jobs: pending/running → failed (error='server_restarted')
    - collections: publishing → draft
    """
    try:
        _startup_conn = get_db()
        try:
            _startup_conn.execute(
                "UPDATE publish_jobs SET status='failed', "
                "error=COALESCE(error, 'server_restarted'), "
                "updated_at=datetime('now') "
                "WHERE status IN ('pending','running')"
            )
            _startup_conn.execute("UPDATE collections SET status='interrupted' WHERE status='publishing'")
            _startup_conn.commit()
            logger.info("Residual publishing jobs reset to interrupted/failed")
        finally:
            _startup_conn.close()
    except Exception as _e:
        logger.warning(f"residual publish job reset failed: {_e}")


async def _startup_rebuild_bm25():
    """BM25 インデックスはメモリ常駐のため、起動時に SQLite chunks から再構築する。
    対象: 1 つ以上の published コレクションを持つワークスペース。
    """
    try:
        from rag import rebuild_bm25_from_db

        conn = get_db()
        try:
            ws_rows = conn.execute(
                "SELECT DISTINCT workspace_id FROM collections " "WHERE workspace_id IS NOT NULL AND workspace_id != ''"
            ).fetchall()
        finally:
            conn.close()
        total_chunks = 0
        for r in ws_rows:
            wid = r["workspace_id"]
            try:
                n = rebuild_bm25_from_db(wid)
                total_chunks += n
            except Exception as e:
                logger.warning(f"BM25 rebuild failed for ws={wid}: {e}")
        logger.info(f"BM25 再構築: {len(ws_rows)}ワークスペース / {total_chunks}チャンク")
    except Exception as e:
        logger.warning(f"BM25 startup rebuild failed: {e}")


async def _startup_reconcile_pgvector_orphans():
    """harden B-1: unified(pgvector) で関係層 chunks に親の無い chunks_vec 行(孤児)を掃除する。

    孤児発生経路: 中断 publish / OOM(ベクター先行 upsert 後・関係層 commit 前に worker 死亡)。
    join は chunks_vec.id == chunks.chunk_id(唯一の writer reembed が chunk_id を id に採用)。
    over-purge 構造不可: NOT EXISTS により「関係層に親のある行」は1行も触らない。
    Chroma(standalone, is_unified=False)は chunks_vec 非使用のため対象外(挙動完全不変)。
    起動毎に走る軽量 idempotent SQL(複数レプリカ同時でも 2 回目は 0 行=安全)。
    """
    try:
        from core.data_layer import get_data_layer

        if not get_data_layer().is_unified:
            return  # Chroma standalone: 対象外(挙動不変)
        conn = get_db()
        try:
            cur = conn.execute(
                "DELETE FROM chunks_vec WHERE NOT EXISTS "
                "(SELECT 1 FROM chunks c WHERE c.chunk_id = chunks_vec.id)"
            )
            removed = cur.rowcount if cur.rowcount is not None else 0
            conn.commit()
        finally:
            conn.close()
        if removed:
            logger.info(f"[harden B-1] pgvector 孤児ベクター再整合: {removed} 行削除(関係層に親なし)")
        else:
            logger.info("[harden B-1] pgvector 孤児ベクター: 0(整合済)")
    except Exception as e:
        # chunks_vec 未作成等は無害 → warning に留め起動継続
        logger.warning(f"[harden B-1] orphan reconcile skipped: {e}")


async def _startup_rebuild_vectors_from_relational():
    """DD-CYN-0116 Z-3: 関係層に本文があるのに索引が空なら、起動時に作り直す。

    種データの取り込み (seed) は関係層だけを移し、ベクターは移さない。∴ 作り直した
    直後や永続領域を失った後は chunks_vec が空になり、検索が全件「該当なし」になる。
    従来これを埋めるのは配備スクリプトが kubectl exec で叩く reembed_pgvector.py だけで、
    しかも失敗は `|| echo WARN` で握り潰されていた（人が走らせた時にしか成立しない）。
    ∴ 実行時の起動経路へ入れる。

    - 関係層 chunks が 0 件、または索引が既に埋まっているときは何もしない（冪等）。
    - 埋め込みを持たない worker(minimal) では動かない → その場で降りる。
    - 複数レプリカが同時に起動しても upsert は冪等なので二重でも壊れない。
    - 既定は「空のときだけ」。CYNOVELA_SKIP_VECTOR_REBUILD=1 で完全に止められる。
    """
    if os.environ.get("CYNOVELA_SKIP_VECTOR_REBUILD") in ("1", "true", "yes"):
        logger.info("[Cynovela] 索引の自動復元: 明示的に無効化されています")
        return
    try:
        from core.data_layer import get_data_layer

        if not get_data_layer().is_unified:
            return  # Chroma standalone: 対象外(挙動不変)
        try:
            import rag as _rag_schema

            _vs_schema = _rag_schema._get_vs()
            if hasattr(_vs_schema, "_ensure_schema"):
                _vs_schema._ensure_schema()
        except Exception as _schema_e:
            logger.warning(f"[Cynovela] 索引の自動復元: chunks_vec schema 準備を継続扱い: {_schema_e}")
        conn = get_db()
        try:
            _n_chunks = conn.execute("SELECT count(*) AS n FROM chunks").fetchone()["n"]
            try:
                _n_vec = conn.execute("SELECT count(*) AS n FROM chunks_vec").fetchone()["n"]
            except Exception:
                _n_vec = 0
        finally:
            conn.close()
        if _n_chunks == 0:
            logger.info("[Cynovela] 索引の自動復元: 本文が0件のため何もしません")
            return
        # 「1行でも在れば済み」としない。途中で止まった復元がそのまま放置され、
        # 以後どの起動でも再開されなくなる (実測でこの穴に落ちた)。
        # 揃うべき件数 = 伏字済みの本文の件数。それに満たなければ足りない分を入れる。
        conn = get_db()
        try:
            _n_expect = conn.execute(
                "SELECT count(*) AS n FROM chunks WHERE tier = 'masked'"
            ).fetchone()["n"]
        finally:
            conn.close()
        if _n_vec >= _n_expect:
            logger.info(
                f"[Cynovela] 索引の自動復元: 揃っています ({_n_vec}/{_n_expect} 行)"
            )
            return

        import rag as _rag_rb

        _ef = _rag_rb._get_chroma_embedding_function()
        if _ef is None:
            logger.warning(
                "[Cynovela] 索引の自動復元: 埋め込みの口がありません(minimal 起動)。"
                f"本文 {_n_chunks} 件に対し索引は空のままです。"
            )
            return

        # レプリカが複数あるので、作り直すのは1つだけにする(同じ仕事を二重にしない)。
        # 印は共有の settings に置き、必ず期限を持たせる。期限が無いと、作り直しの
        # 途中でその Pod が落ちたときに印が 'running' のまま残り、以後どのレプリカも
        # 拾えなくなる (実測済み: 索引が空のまま誰も直さない状態になった)。
        if not _claim_vector_rebuild():
            logger.info("[Cynovela] 索引の自動復元: 別のレプリカが実行中のため見送ります")
            return

        logger.warning(
            f"[Cynovela] 索引が足りません({_n_vec}/{_n_expect} 行)。裏で続きを作ります。"
            "件数が多いと時間がかかります(進み具合はログに出します)。"
            "待ち受けは止めません。"
        )

        # ★ 待ち受けを塞がないこと。ここで await すると uvicorn が受け付けを始める前に
        #   何時間も止まり、readiness/liveness が落ちて Pod が kill される
        #   (実測: exit 137・39,489 件で毎秒0.5件=およそ20時間)。
        #   ∴ 後ろで走らせ、起動はすぐ先へ進める。
        async def _bg():
            try:
                _n = await asyncio.to_thread(_rebuild_vectors_from_relational_sync)
                logger.info(f"[Cynovela] 索引の自動復元: {_n} 件を入れ直しました")
            except Exception as _e:
                logger.warning(f"[Cynovela] 索引の自動復元が途中で終わりました: {_e}")
            finally:
                _release_vector_rebuild()

        asyncio.create_task(_bg())
    except Exception as e:
        logger.warning(f"[Cynovela] 索引の自動復元に失敗(起動は継続): {e}")


_VECTOR_REBUILD_KEY = "vector.rebuild.claim"
# 印の期限。これを過ぎた印は「持ち主が落ちた」とみなして横取りしてよい。
# 走行中は _touch_vector_rebuild() が定期的に押し直すので、生きている限り横取りされない。
_VECTOR_REBUILD_TTL_SEC = 600
_VECTOR_REBUILD_OWNER: str | None = None


def _vector_rebuild_owner() -> str:
    """この Pod の名前。K8s では HOSTNAME がそのまま Pod 名になる。"""
    import os as _os
    import uuid as _uuid

    global _VECTOR_REBUILD_OWNER
    if _VECTOR_REBUILD_OWNER is None:
        _VECTOR_REBUILD_OWNER = _os.environ.get("HOSTNAME") or f"api-{_uuid.uuid4().hex[:8]}"
    return _VECTOR_REBUILD_OWNER


def _claim_vector_rebuild() -> bool:
    """索引の作り直しを1レプリカだけが行うための印を取る。

    印の値は "<持ち主>|<時刻>"。持ち主を書くのは、入れ替えのときに
    「終了しかけている古い Pod が、新しい Pod の取った印を解いてしまう」事故を
    防ぐため (実測で起きた: 解放が無条件だったので2本とも走り出した)。
    期限切れの印は横取りする (持ち主が途中で落ちた場合の詰まりを自力で解く)。
    """
    import time as _t

    _now = _t.time()
    _me = _vector_rebuild_owner()
    _mine = f"{_me}|{_now}"
    conn = get_db()
    try:
        _row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_VECTOR_REBUILD_KEY,)
        ).fetchone()
        if _row is None:
            try:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?)",
                    (_VECTOR_REBUILD_KEY, _mine),
                )
                conn.commit()
                return True
            except Exception:
                # 同時に別のレプリカが入れた (key は主キー)。取れなかった扱い。
                conn.rollback()
                return False
        _v = _row["value"] or ""
        _owner, _, _ts = _v.partition("|")
        if _v == "done" or not _ts:
            _stale = True
        else:
            try:
                _stale = (_now - float(_ts)) > _VECTOR_REBUILD_TTL_SEC
            except Exception:
                _stale = True  # 読めない値は期限切れ扱い
        if not _stale:
            return False
        # 横取り: 値が変わっていないことを条件に更新する (同時に2つが取らない)
        _cur = conn.execute(
            "UPDATE settings SET value = ? WHERE key = ? AND value = ?",
            (_mine, _VECTOR_REBUILD_KEY, _v),
        )
        conn.commit()
        if getattr(_cur, "rowcount", 0) == 1:
            if _v != "done":
                logger.warning(
                    f"[Cynovela] 索引の自動復元: 期限切れの印を引き取りました "
                    f"(前の持ち主 {_owner or '不明'} が途中で落ちたとみなします)"
                )
            return True
        return False
    finally:
        conn.close()


def _touch_vector_rebuild() -> None:
    """走行中であることを示すため印を押し直す。自分が持ち主のときだけ。"""
    import time as _t

    _me = _vector_rebuild_owner()
    try:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value LIKE ?",
                (f"{_me}|{_t.time()}", _VECTOR_REBUILD_KEY, f"{_me}|%"),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _release_vector_rebuild() -> None:
    """印を解く。自分が持ち主のときだけ。

    無条件に解くと、入れ替えのときに終了しかけている古い Pod が、新しい Pod の
    取った印を解いてしまい、2本とも同じ作り直しを走らせる (実測で起きた)。
    """
    _me = _vector_rebuild_owner()
    try:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE settings SET value = ? WHERE key = ? AND value LIKE ?",
                ("done", _VECTOR_REBUILD_KEY, f"{_me}|%"),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass


def _rebuild_vectors_from_relational_sync() -> int:
    """関係層 chunks から索引を作り直す(同期・別スレッドで走らせる)。

    deploy/k8s/phase4a/reembed_pgvector.py と同じ作り。保存は元のまま
    (raw は enc: のまま・masked は平文)、埋め込みだけ平文から作る。
    """
    import time
    from collections import defaultdict
    import rag as _rag_rb
    from vault_enc import dec_raw as _dec_raw

    _ef = _rag_rb._get_chroma_embedding_function()
    _vs = _rag_rb._get_vs()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT chunk_id, content, collection_id, workspace_id, tier, source_doc FROM chunks"
        ).fetchall()
    finally:
        conn.close()

    # 既に索引に在る id は飛ばす (途中で止まっても続きから再開できるように)。
    _have: set = set()
    try:
        conn = get_db()
        try:
            for _r in conn.execute("SELECT id FROM chunks_vec").fetchall():
                _have.add(_r["id"])
        finally:
            conn.close()
    except Exception as _e:
        logger.warning(f"[Cynovela] 索引の自動復元: 既存 id を読めません (全件やり直します): {_e}")
    if _have:
        logger.info(f"[Cynovela] 索引の自動復元: 既に入っている {len(_have)} 件は飛ばします")

    groups = defaultdict(list)
    _skipped_raw = 0
    for r in rows:
        if r["chunk_id"] in _have:
            continue
        _tier = r["tier"] or "raw"
        # masked-only: 伏字前の層へは書かない。書かない層の埋め込みを計算するのは
        # 丸ごと無駄なので、ここで先に落とす (実測で本文の約半分が raw だった。
        # 従来は計算してから upsert 側で捨てており、所要が倍になっていた)。
        if _tier != "masked":
            _skipped_raw += 1
            continue
        groups[(r["collection_id"], _tier)].append(r)
    if _skipped_raw:
        logger.info(
            f"[Cynovela] 索引の自動復元: 伏字前の層 {_skipped_raw} 件は対象外 (masked-only)"
        )

    total = 0
    _grand = sum(len(v) for v in groups.values())
    _t0 = time.time()
    _last_log = _t0
    for (cid, tier), items in groups.items():
        B = 32
        for i in range(0, len(items), B):
            # 進み具合を定期的に出す (何時間もかかる場合に「止まっている」と誤解させない)
            _now = time.time()
            if _now - _last_log > 60:
                _rate = total / max(_now - _t0, 1e-6)
                _left = (_grand - total) / _rate if _rate > 0 else -1
                logger.info(
                    f"[Cynovela] 索引の自動復元: {total}/{_grand} 件 "
                    f"({_rate:.1f} 件/秒・残り およそ {_left / 60:.0f} 分)"
                )
                _last_log = _now
                _touch_vector_rebuild()  # 生きている印を押し直す (横取りされないように)
            batch = items[i : i + B]
            plain, docs, ids, metas = [], [], [], []
            for r in batch:
                c = r["content"] or ""
                doc = c
                # masked 層は平文で入っている。念のため暗号文のまま来たものは埋め込みに
                # 使わない (enc-leak-guard と同じ考え方)。
                if c.startswith("enc:"):
                    try:
                        c = _dec_raw(c)
                    except Exception:
                        pass
                    if c.startswith("enc:"):
                        continue  # 復号できないものは索引に入れない
                plain.append(c)
                docs.append(doc)
                ids.append(r["chunk_id"])
                metas.append(
                    {
                        "workspace_id": r["workspace_id"],
                        "source_doc": r["source_doc"],
                        "chunk_id": r["chunk_id"],
                    }
                )
            if not plain:
                continue  # この束は全部落ちた (空を埋め込みに渡さない)
            embs = [[float(x) for x in e] for e in _ef(plain)]
            _vs.upsert(cid, ids=ids, documents=docs, metadatas=metas, embeddings=embs, tier=tier)
            total += len(ids)  # 実際に入れた件数を数える (束の長さではない)
    return total


# ════════════════════════════════════════════════════════════
# P4 Block 4: Alerts (poll endpoint)
# ════════════════════════════════════════════════════════════
# /api/alerts は routers/alerts.py に移動済み


# ════════════════════════════════════════════════════════════
# P4 Block 5: Guardrails — PII detection list, blocked topics
# ════════════════════════════════════════════════════════════
# /api/guardrails/* は routers/guardrails.py に移動済み


def check_blocked_topics(text: str) -> dict:
    """text に登録済み禁止トピックが含まれるかチェック.

    Returns: {"detected": bool, "topic": str?, "action": str?, "name": str?}
    """
    if not text:
        return {"detected": False}
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name, pattern, is_regex, action " "FROM blocked_topics WHERE is_active = 1"
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        try:
            if r["is_regex"]:
                if re.search(r["pattern"], text, re.IGNORECASE):
                    return {"detected": True, "name": r["name"], "topic": r["pattern"], "action": r["action"]}
            else:
                if r["pattern"].lower() in text.lower():
                    return {"detected": True, "name": r["name"], "topic": r["pattern"], "action": r["action"]}
        except Exception:
            continue
    return {"detected": False}


# ════════════════════════════════════════════════════════════
# /api/catalog は別 router に移動済み


# /api/documents/{document_id}/metadata (PATCH) は routers/files.py に移動済み


def enrich_document_metadata(document_id: str, text: str) -> dict:
    """メタデータエンジンによる自動分類 + 感度スコアリング.

    Publish 時または外部呼び出しで使う。失敗してもサイレント。
    """
    try:
        from utils.metadata import classify_document_type, score_sensitivity

        dt = classify_document_type(text)
        ss = score_sensitivity(text)
        conn = get_db()
        try:
            conn.execute(
                """UPDATE files SET
                       doc_type = ?,
                       sensitivity_level = ?,
                       sensitivity_score = ?,
                       metadata_enriched_at = datetime('now')
                   WHERE id = ?""",
                (dt["type"], ss["level"], ss["score"], document_id),
            )
            conn.commit()
        finally:
            conn.close()
        return {"doc_type": dt["type"], "sensitivity_level": ss["level"], "sensitivity_score": ss["score"]}
    except Exception as e:
        logger.warning(f"enrich_document_metadata failed: {e}")
        return {}


# ════════════════════════════════════════════════════════════
# P5 Block 1: Version Compare — 2 Collection を同じ質問で比較
# 既存 /api/chat/compare (model_a vs model_b) と分けるため別パスを使う
# ════════════════════════════════════════════════════════════
# _resolve_active_llm は routers/chat.py または core/llm.py に移動済み


# _call_llm_simple は routers/chat.py または core/llm.py に移動済み


# /api/chat/compare-collections (POST) は routers/chat.py に移動済み


# ════════════════════════════════════════════════════════════
# P5 Block 2: AI Self-observation reports
# ════════════════════════════════════════════════════════════
def collect_report_stats(days: int = 30) -> dict:
    """audit_logs 等から運用統計を収集。JSON 化されていない値は 0 扱い."""
    days = max(1, min(int(days or 30), 365))
    conn = get_db()
    try:

        def _scalar(sql: str, params: tuple = ()) -> float:
            try:
                row = conn.execute(sql, params).fetchone()
                return float(row[0]) if row and row[0] is not None else 0.0
            except Exception:
                return 0.0

        def _int(sql: str, params: tuple = ()) -> int:
            try:
                row = conn.execute(sql, params).fetchone()
                return int(row[0] or 0) if row else 0
            except Exception:
                return 0

        cutoff = f"-{days} days"
        total_queries = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action IN ('chat_query','RAG_QUERY') "
            "AND timestamp >= datetime('now', ?)",
            (cutoff,),
        )
        avg_faith = _scalar(
            "SELECT AVG(CAST(json_extract(detail, '$.faithfulness') AS REAL)) "
            "FROM audit_logs WHERE action IN ('chat_query','RAG_QUERY') "
            "AND timestamp >= datetime('now', ?) "
            "AND json_extract(detail, '$.faithfulness') IS NOT NULL",
            (cutoff,),
        )
        zero_total = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action IN ('chat_query','RAG_QUERY') "
            "AND timestamp >= datetime('now', ?)",
            (cutoff,),
        )
        zero_hits = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action IN ('chat_query','RAG_QUERY') "
            "AND timestamp >= datetime('now', ?) "
            "AND CAST(json_extract(detail, '$.top_score') AS REAL) < 0.3",
            (cutoff,),
        )
        guardrail_count = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action IN ('GUARDRAIL_TRIGGERED','PROMPT_INJECTION_BLOCKED') "
            "AND timestamp >= datetime('now', ?)",
            (cutoff,),
        )
        pii_count = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action IN ('PII_DETECTED','pii_detected') "
            "AND timestamp >= datetime('now', ?)",
            (cutoff,),
        )
        injection_blocked = _int(
            "SELECT COUNT(*) FROM audit_logs "
            "WHERE action = 'PROMPT_INJECTION_BLOCKED' "
            "AND timestamp >= datetime('now', ?)",
            (cutoff,),
        )
        # active_users: audit_logs.target または detail の user_id を計上
        active_users = _int(
            "SELECT COUNT(DISTINCT COALESCE("
            "json_extract(detail, '$.user_id'), target)) FROM audit_logs "
            "WHERE timestamp >= datetime('now', ?) "
            "AND COALESCE(json_extract(detail, '$.user_id'), target) != ''",
            (cutoff,),
        )
        try:
            docs_published = _int(
                "SELECT COUNT(*) FROM document_provenance " "WHERE published_at >= datetime('now', ?)", (cutoff,)
            )
        except Exception:
            docs_published = 0
        # top collections (上位 3 件)
        top_cols: list = []
        try:
            top_cols_rows = conn.execute(
                "SELECT target AS col_id, COUNT(*) AS cnt FROM audit_logs "
                "WHERE action = 'chat_query' "
                "AND timestamp >= datetime('now', ?) "
                "AND target IS NOT NULL "
                "GROUP BY target ORDER BY cnt DESC LIMIT 3",
                (cutoff,),
            ).fetchall()
            top_cols = [{"col_id": r["col_id"], "cnt": int(r["cnt"])} for r in top_cols_rows]
        except Exception:
            top_cols = []
    finally:
        conn.close()
    return {
        "days": days,
        "total_queries": total_queries,
        "avg_faithfulness": round(avg_faith, 3),
        "zero_hit_rate": round((zero_hits / zero_total) if zero_total else 0.0, 3),
        "guardrail_count": guardrail_count,
        "pii_count": pii_count,
        "injection_blocked": injection_blocked,
        "active_users": active_users,
        "docs_published": docs_published,
        "top_collections": top_cols,
    }


_REPORT_PROMPTS = {
    "monthly": """You are a system analyst reviewing an AI data governance platform's operational log.
Generate a concise monthly operations report based on the following statistics.
Write in clear, professional English. Keep it under 400 words.

IMPORTANT: This is a self-observation log generated by the system itself.
Always acknowledge uncertainty and recommend human review.

Statistics (last {days} days):
- Total RAG queries: {total_queries}
- Average faithfulness score: {avg_faithfulness}
- Zero-hit rate: {zero_hit_rate}
- Guardrail triggers: {guardrail_count}
- PII detections: {pii_count}
- Prompt injection attempts blocked: {injection_blocked}
- Active users: {active_users}
- Documents published: {docs_published}
- Top collections by usage: {top_collections}

Generate sections:
1. Executive Summary (2-3 sentences)
2. Usage Highlights
3. Quality & Safety Observations
4. Recommendations
5. Note: This report was auto-generated. Human review recommended.""",
    "poc": """Generate a PoC results summary for this AI RAG governance platform.
Target audience: technical evaluators. Keep it under 300 words.

Statistics (last {days} days):
{stats_block}

Sections:
1. What was tested
2. Key metrics
3. Governance features validated
4. Suggested next steps
5. Note: Auto-generated. Human review recommended before sharing externally.""",
    "technical": """Generate a technical health summary for engineering review.
Keep it under 300 words. Focus on system performance and data quality.

Statistics:
{stats_block}

Sections:
1. System Health
2. RAG Quality Metrics
3. Storage & Performance
4. Issues & Alerts
5. Note: Auto-generated. Verify against raw logs before acting.""",
}


# /api/reports/* は routers/reports.py に移動済み


# ════════════════════════════════════════════════════════════
# P5 Block 3: Chat handoff summarization
# ════════════════════════════════════════════════════════════
# /api/chat/summarize (POST) は routers/chat.py に移動済み


# ─── Auth API ───

# /api/auth/users, /api/auth/login, /api/auth/logout, /api/auth/me は
# routers/auth.py に移動済み


# UX-2: ユーザー表示名/role の更新 (admin or 本人のみ)
# /api/users/{user_id} PATCH は routers/users.py に移動済み


# ─── BLOCK 2: ユーザー管理API（admin限定） ───
# VALID_ROLES は core/constants.py に移動済み


# /api/admin/users (GET/POST/PATCH/DELETE) と /api/admin/users/{user_id}/reset-password は
# routers/admin.py に移動済み


# ─── BLOCK 3: バックアップ・リカバリAPI（admin限定） ───

import shutil as _shutil
from pathlib import Path as _Path

# FIX-4 (Critical): Mock版や独立配置のため、CYNOVELA_BACKUP_DIR / CYNOVELA_DB 環境変数を尊重
# alpha §9-A-7: バックアップ対象 DB を実 DB と一致させる (パッケージ配下 db/cynovela.db)
BACKUP_BASE = _Path(os.path.expanduser(os.environ.get("CYNOVELA_BACKUP_DIR", "~/.cynovela/backups")))
_SRV_APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH_FOR_BACKUP = _Path(os.path.expanduser(os.environ.get("CYNOVELA_DB", os.path.join(_SRV_APP_DIR, "db", "cynovela.db"))))


def _resolve_chroma_path() -> _Path:
    """ChromaDBの実体パスを config / rag.py / 既知のパスから解決する。"""
    try:
        from core.config import CYNOVELA_CONFIG as _CFG

        p = (_CFG.get("vector_store") or {}).get("path", "")
        if p:
            return _Path(p).resolve()
    except Exception:
        pass
    try:
        from rag import CHROMA_PATH as _RP

        return _Path(_RP).resolve()
    except Exception:
        pass
    return _Path("./data/chroma").resolve()


CHROMA_PATH_FOR_BACKUP = _resolve_chroma_path()


def _create_backup(label: str = "") -> dict:
    BACKUP_BASE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_label = "".join(c for c in (label or "") if c.isalnum() or c in "-_")[:32]
    name = f"backup-{ts}" + (f"-{safe_label}" if safe_label else "")
    backup_dir = BACKUP_BASE / name
    backup_dir.mkdir(parents=True, exist_ok=True)
    # SQLite WALチェックポイント後コピー
    try:
        conn = get_db()
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            conn.close()
    except Exception:
        pass
    # release/v1.0.0-alpha: バックアップ書き込みは cynovela.db / chroma 命名で統一
    if DB_PATH_FOR_BACKUP.exists():
        _shutil.copy2(str(DB_PATH_FOR_BACKUP), str(backup_dir / "cynovela.db"))
    if CHROMA_PATH_FOR_BACKUP.exists():
        _shutil.copytree(str(CHROMA_PATH_FOR_BACKUP), str(backup_dir / "chroma"))
    db_size = (backup_dir / "cynovela.db").stat().st_size if (backup_dir / "cynovela.db").exists() else 0
    chroma_size = (
        sum(p.stat().st_size for p in (backup_dir / "chroma").rglob("*") if p.is_file())
        if (backup_dir / "chroma").exists()
        else 0
    )
    meta = {
        "name": name,
        "label": label or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "db_size": db_size,
        "chroma_size": chroma_size,
    }
    (backup_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def _list_backups() -> list:
    if not BACKUP_BASE.exists():
        return []
    out = []
    for d in sorted(BACKUP_BASE.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if meta_path.exists():
            try:
                out.append(json.loads(meta_path.read_text()))
                continue
            except Exception:
                pass
        out.append({"name": d.name, "label": "", "created_at": "", "db_size": 0, "chroma_size": 0})
    return out


def _restore_backup(name: str) -> dict:
    backup_dir = BACKUP_BASE / name
    if not backup_dir.exists() or not backup_dir.is_dir():
        raise HTTPException(404, "バックアップが見つかりません")
    if backup_dir.resolve().parent != BACKUP_BASE.resolve():
        raise HTTPException(400, "不正なパスです")
    # release/v1.0.0-alpha: cynovela.db / chroma 命名を先に探し、旧 v13/v12 命名にフォールバック（後方互換）
    db_src = backup_dir / "cynovela.db"
    chroma_src = backup_dir / "chroma"
    if not db_src.exists():
        for _legacy_db, _legacy_chroma in [("v13.db", "v13-chromadb"), ("v12.db", "v12-chromadb")]:
            _db_legacy = backup_dir / _legacy_db
            if _db_legacy.exists():
                db_src = _db_legacy
                chroma_src = backup_dir / _legacy_chroma
                break
        else:
            raise HTTPException(400, "バックアップが破損しています (cynovela.db / v13.db / v12.db いずれも見つかりません)")
    # 現状を before-restore として自動バックアップ
    auto_meta = _create_backup(label="before-restore")
    # 復元
    _shutil.copy2(str(db_src), str(DB_PATH_FOR_BACKUP))
    if chroma_src.exists():
        if CHROMA_PATH_FOR_BACKUP.exists():
            _shutil.rmtree(str(CHROMA_PATH_FOR_BACKUP), ignore_errors=True)  # chaos-E: live chromadb 競合での消失ファイル unlink 500 を回避 (全置換のため無害)
        _shutil.copytree(str(chroma_src), str(CHROMA_PATH_FOR_BACKUP), dirs_exist_ok=True)  # dirs_exist_ok: rmtree 残存 dir を許容
    # BM25インデックスをクリア
    try:
        from rag import _bm25_indexes

        _bm25_indexes.clear()
    except Exception:
        pass
    return {"ok": True, "restored_from": name, "auto_backup_before_restore": auto_meta["name"]}


def _delete_backup(name: str) -> None:
    backup_dir = BACKUP_BASE / name
    if not backup_dir.exists() or not backup_dir.is_dir():
        raise HTTPException(404, "バックアップが見つかりません")
    if backup_dir.resolve().parent != BACKUP_BASE.resolve():
        raise HTTPException(400, "不正なパスです")
    _shutil.rmtree(str(backup_dir))


# /api/admin/backup (POST/GET/restore/DELETE) は routers/admin.py に移動済み


# ─── Data Sources API ───

# /api/sources/* (list/get/create/open-in-finder) は routers/sources.py に移動済み


# S-3 (Smart Ingestion): スキャン時の自動分類用 classifier (singleton)
# LightweightClassifier はステートレス・キーワードマッチのみで CPU 極小。
# モジュールレベルで保持して再インスタンス化コストを削減。
from utils.metadata.classification import get_classifier as _get_classifier

_FILE_CLASSIFIER = _get_classifier("lightweight")

_scan_cancel_flags: dict[str, bool] = {}


def _claim_scan(source_id: str) -> bool:
    """C-6: 同一 source への走査開始の排他クレーム (正はここ1点)。

    `UPDATE ... WHERE id=? AND status <> 'scanning'` の rowcount で原子的に
    status='scanning' を取る。read-then-act の TOCTOU を作らない。
    0 行 = 「既に走査中」または「source が存在しない」→ 開始しない。
    クレーム成功時は cancel_requested も 0 へ戻す (新走査の開始なので、
    開始前に残っていた古い中止要求は持ち越さない)。
    全開始経路 (同期 /scan・/scan/async・auto_scan・worker) がここを通る。
    """
    conn = get_db()
    try:
        try:
            cur = conn.execute(
                "UPDATE sources SET status = 'scanning', cancel_requested = 0 "
                "WHERE id = ? AND status <> 'scanning'",
                (source_id,),
            )
        except Exception:
            # migration 0009 未適用 (cancel_requested 列が無い Postgres 等) でも
            # 排他だけは効かせる (列なし版へフォールバック)。
            try:
                conn.rollback()
            except Exception:
                pass
            cur = conn.execute(
                "UPDATE sources SET status = 'scanning' WHERE id = ? AND status <> 'scanning'",
                (source_id,),
            )
        claimed = (getattr(cur, "rowcount", 0) or 0) > 0
        conn.commit()
    finally:
        conn.close()
    return claimed


def _scan_cancel_requested(source_id: str) -> bool:
    """C-11: 走査の中止要求の判定 (プロセス内フラグ OR DB の cancel_requested)。

    走査本体の conn は書き込みトランザクション中でスナップショットが固まっており、
    他プロセス (API Pod / worker) が commit した cancel_requested=1 が見えない。
    ∴ DB 側は毎回短命の別接続で最新の committed 値を読む。
    列が無い (migration 未適用) 等の失敗は「中止要求なし」に倒す。
    """
    if _scan_cancel_flags.get(source_id):
        return True
    try:
        _c = get_db()
        try:
            _row = _c.execute(
                "SELECT cancel_requested FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        finally:
            _c.close()
        return bool(_row and _row["cancel_requested"])
    except Exception:
        return False


# /api/sources/{source_id}/scan/cancel は routers/sources.py に移動済み


def _start_scan(source_id: str) -> None:
    """Phase 2: バックグラウンド scan の起動点。

    queue 有効なら Redis に積んで worker が消費（durable）。無効（standalone 既定）なら
    従来どおりプロセス内 daemon スレッドで実行＝挙動不変。
    """
    try:
        from core.job_queue import queue_enabled, enqueue

        if queue_enabled():
            enqueue({"id": "scan-" + new_id(), "type": "scan", "source_id": source_id})
            return
    except Exception as _e:
        logger.warning(f"[scan] enqueue 失敗、スレッドにフォールバックします: {_e}")
    threading.Thread(target=_do_scan, args=(source_id,), daemon=True).start()


def _do_scan(source_id: str, already_claimed: bool = False):
    """Execute scan on a source: walk directory, register files, classify.

    C-6: 開始には _claim_scan の排他クレームが必須。API 経路 (同期 /scan・/scan/async) は
    409 を返すため呼び元でクレーム済み (already_claimed=True) で入る。それ以外
    (auto_scan スレッド fallback・queue worker・ポーリング・デモ seed) はここでクレームし、
    取れなければ二重走査としてログを出してスキップする。
    """
    # C-6: 排他クレーム (status='scanning' の原子的な取得)。従来ここにあった
    # 「UPDATE sources SET status='scanning'」はクレームに統合した (二重書き込みしない)。
    if not already_claimed:
        if not _claim_scan(source_id):
            logger.info(
                f"[scan] クレーム失敗のためスキップ src={source_id} (走査中の二重開始、または source 不存在)"
            )
            return
    # BLOCK B-6: スキャン開始時にcancel flagをクリア
    # (DB 側の cancel_requested は _claim_scan が 0 へ戻している = 新走査の開始)
    _scan_cancel_flags.pop(source_id, None)
    conn = get_db()
    try:
        source = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not source:
            conn.close()
            return

        # FIX-045: _do_scan を BEGIN IMMEDIATE で開始し原子性を確保 (cancel/例外時の partial state 削減)。
        # 既存の中間 commit は維持 (大量ファイル scan の途中可視性を保つため)、最小実装に留める。
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            # 既に transaction 中 / busy 等は黙過 (既存挙動を維持)
            pass

        src_path = os.path.abspath(os.path.expanduser(source["path"]))
        if not os.path.exists(src_path):
            conn.execute("UPDATE sources SET status = 'failed' WHERE id = ?", (source_id,))
            conn.commit()
            conn.close()
            raise HTTPException(400, f"パスが見つかりません: {src_path}")

        # Phase F: 安定 file_id（path から導出）に切替。再スキャンで file_id が変わると
        # collection_files が孤立して再Publishが空になるため。
        import hashlib as _hl

        def _stable_fid(path: str) -> str:
            # PORTABILITY FIX: NFC 正規化を入れて macOS(NFD) / Linux/Windows(NFC) 間の
            # ファイル名揺れで file_id が変わるのを防ぐ（教訓1相当のサイレント不一致防止）
            _p = unicodedata.normalize("NFC", path)
            return _hl.md5(f"{source_id}|{_p}".encode(), usedforsecurity=False).hexdigest()[:16]

        seen_paths: set[str] = set()

        file_count = 0
        try:
            if os.path.isfile(src_path):
                ext = os.path.splitext(src_path)[1].lower()
                if ext in SUPPORTED_EXTENSIONS:
                    fname = os.path.basename(src_path)
                    fsize = os.path.getsize(src_path)
                    mime = mimetypes.guess_type(src_path)[0] or "application/octet-stream"
                    text = extract_text(src_path)
                    categories = classify_file(src_path, text) if text else []
                    # S-3: Smart Ingestion 自動分類 (Lightweight、LLM不使用、決定論的)
                    try:
                        _cls = _FILE_CLASSIFIER.classify(fname, src_path, (text or "")[:500])
                    except Exception as _ce:
                        logger.warning(f"classification 失敗 ({fname}): {_ce}")
                        _cls = {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "lightweight"}
                    _cls_json = json.dumps(_cls, ensure_ascii=False)
                    _cls_at = datetime.now().isoformat(timespec="seconds")
                    # P5-B: メタデータエンジン
                    _meta = (
                        classify_metadata(src_path, text or "")
                        if is_feature_enabled("metadata_engine")
                        else {
                            "doc_type": "general",
                            "sensitivity": "public",
                            "sensitivity_score": 0.0,
                            "department": "",
                            "owner": "",
                            "auto_tags": [],
                        }
                    )
                    fid = _stable_fid(src_path)
                    seen_paths.add(src_path)
                    conn.execute(
                        """
                        INSERT INTO files (id, source_id, name, path, size, mime_type, categories,
                                           doc_type, sensitivity, sensitivity_score, auto_tags, owner, department,
                                           classification, classified_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            source_id=excluded.source_id, name=excluded.name, path=excluded.path,
                            size=excluded.size, mime_type=excluded.mime_type, categories=excluded.categories,
                            doc_type=excluded.doc_type, sensitivity=excluded.sensitivity,
                            sensitivity_score=excluded.sensitivity_score, auto_tags=excluded.auto_tags,
                            owner=excluded.owner, department=excluded.department,
                            classification=excluded.classification, classified_at=excluded.classified_at,
                            missing=0
                        """,
                        (
                            fid,
                            source_id,
                            fname,
                            src_path,
                            fsize,
                            mime,
                            json.dumps(categories),
                            _meta["doc_type"],
                            _meta["sensitivity"],
                            _meta["sensitivity_score"],
                            json.dumps(_meta["auto_tags"], ensure_ascii=False),
                            _meta["owner"],
                            _meta["department"],
                            _cls_json,
                            _cls_at,
                        ),
                    )
                    if "PII" in categories:
                        _log_audit(
                            conn, "pii_detected", target=os.path.basename(src_path), detail=f"PII detected in {fname}"
                        )
                    file_count = 1
            elif os.path.isdir(src_path):
                # C-11: 中止 = プロセス内フラグ OR DB の cancel_requested。
                # DB 側はフォルダ毎 + 25ファイル毎に短命接続でポーリングする
                # (worker プロセスで走る走査にも API からの中止が届くように)。
                _canceled = False
                _files_since_poll = 0
                for root, dirs, filenames in os.walk(src_path):
                    # BLOCK B-6/C-11: 中止要求をループ毎に確認して即時中断
                    if _scan_cancel_requested(source_id):
                        _canceled = True
                        break
                    for fname in filenames:
                        _files_since_poll += 1
                        if _files_since_poll >= 25:
                            _files_since_poll = 0
                            # 進み具合 (file_count) を途中 commit で可視化しつつ、
                            # 書き込みロックも一旦解放する (別接続の cancel UPDATE を通す)
                            conn.execute(
                                "UPDATE sources SET file_count = ? WHERE id = ?",
                                (file_count, source_id),
                            )
                            conn.commit()
                            if _scan_cancel_requested(source_id):
                                _canceled = True
                                break
                        elif _scan_cancel_flags.get(source_id):
                            _canceled = True
                            break
                        ext = os.path.splitext(fname)[1].lower()
                        if ext not in SUPPORTED_EXTENSIONS:
                            continue
                        # NFD/NFC: uploads named volume はバイト厳密照合のため、FS アクセスは
                        # walk が返した生パスを使う（NFC 変換パスは実在しない）。file_id の
                        # 安定性は _stable_fid 内部の NFC 正規化で従来どおり担保される。
                        fpath = os.path.join(root, fname)
                        fsize = os.path.getsize(fpath)
                        mime = mimetypes.guess_type(fpath)[0] or "application/octet-stream"

                        # Extract text and classify
                        text = extract_text(fpath)
                        categories = classify_file(fpath, text)
                        # S-3: Smart Ingestion 自動分類 (Lightweight、LLM不使用、決定論的)
                        try:
                            _cls = _FILE_CLASSIFIER.classify(fname, fpath, (text or "")[:500])
                        except Exception as _ce:
                            logger.warning(f"classification 失敗 ({fname}): {_ce}")
                            _cls = {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "lightweight"}
                        _cls_json = json.dumps(_cls, ensure_ascii=False)
                        _cls_at = datetime.now().isoformat(timespec="seconds")
                        # P5-B: メタデータエンジン
                        _meta = (
                            classify_metadata(fpath, text or "")
                            if is_feature_enabled("metadata_engine")
                            else {
                                "doc_type": "general",
                                "sensitivity": "public",
                                "sensitivity_score": 0.0,
                                "department": "",
                                "owner": "",
                                "auto_tags": [],
                            }
                        )

                        fid = _stable_fid(fpath)
                        seen_paths.add(fpath)
                        conn.execute(
                            """
                            INSERT INTO files (id, source_id, name, path, size, mime_type, categories,
                                               doc_type, sensitivity, sensitivity_score, auto_tags, owner, department,
                                               classification, classified_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                source_id=excluded.source_id, name=excluded.name, path=excluded.path,
                                size=excluded.size, mime_type=excluded.mime_type, categories=excluded.categories,
                                doc_type=excluded.doc_type, sensitivity=excluded.sensitivity,
                                sensitivity_score=excluded.sensitivity_score, auto_tags=excluded.auto_tags,
                                owner=excluded.owner, department=excluded.department,
                                classification=excluded.classification, classified_at=excluded.classified_at,
                                missing=0
                            """,
                            (
                                fid,
                                source_id,
                                fname,
                                fpath,
                                fsize,
                                mime,
                                json.dumps(categories),
                                _meta["doc_type"],
                                _meta["sensitivity"],
                                _meta["sensitivity_score"],
                                json.dumps(_meta["auto_tags"], ensure_ascii=False),
                                _meta["owner"],
                                _meta["department"],
                                _cls_json,
                                _cls_at,
                            ),
                        )

                        # Log PII detection
                        if "PII" in categories:
                            _log_audit(
                                conn, "pii_detected", target=os.path.basename(fpath), detail=f"PII detected in {fname}"
                            )

                        file_count += 1
                    if _canceled:
                        break

                if _canceled:
                    # C-11: 中止の終端は 'canceled' (実失敗 'failed' と区別する)。
                    # 処理済み件数と時刻を残し、中止要求は消費済みとして 0 へ戻す。
                    conn.execute(
                        "UPDATE sources SET status = 'canceled', file_count = ?, "
                        "last_scanned = ?, cancel_requested = 0 WHERE id = ?",
                        (file_count, datetime.now().isoformat(), source_id),
                    )
                    _log_audit(
                        conn, "scan_canceled", target=source_id, detail=f"processed={file_count}"
                    )
                    conn.commit()
                    _scan_cancel_flags.pop(source_id, None)
                    return

            # intake-togo-v2-20260705 (Fix 7): disk 上から消えたファイルは削除せず missing=1 を立てる（非破壊）。
            # 旧実装の DELETE は collection_files を CASCADE で消し、scan のみ実行時に chunks/Chroma が
            # 宙吊りになる欠陥があった。実体が再出現したら上の upsert が missing=0 に戻す。
            existing_paths = [
                r["path"] for r in conn.execute("SELECT path FROM files WHERE source_id = ?", (source_id,)).fetchall()
            ]
            for old_path in existing_paths:
                if old_path not in seen_paths:
                    conn.execute("UPDATE files SET missing = 1 WHERE source_id = ? AND path = ?", (source_id, old_path))

            conn.execute(
                "UPDATE sources SET status = 'completed', file_count = ?, last_scanned = ? WHERE id = ?",
                (file_count, datetime.now().isoformat(), source_id),
            )
        except Exception as e:
            # 失敗理由を無痕跡にしない: アプリログ + audit_logs へ記録した上で failed 化
            logger.error(f"scan 失敗 src={source_id}: {type(e).__name__}: {e}")
            conn.execute("UPDATE sources SET status = 'failed' WHERE id = ?", (source_id,))
            try:
                _log_audit(conn, "scan_failed", target=source_id, detail=f"{type(e).__name__}: {e}", result="failure")
            except Exception:
                pass

        conn.commit()
        # C-11: 終端 (completed/failed) でも中止要求の残渣を消費する。走査の最終盤に届いた
        # cancel はもう効かせようがないため、次回走査へ持ち越さず 0 へ戻す (別トランザクション
        # なので、失敗しても直前の終端 status は保たれる。列なしの未移行 DB は黙過)。
        try:
            conn.execute("UPDATE sources SET cancel_requested = 0 WHERE id = ?", (source_id,))
            conn.commit()
        except Exception:
            pass
    except BaseException:
        # C-6: クレーム済みの status='scanning' を例外時に宙吊りにしない (終端保証)。
        # 走行中の内側 try が拾えない例外 (SELECT 失敗・パス不在の HTTPException 等) でも、
        # まだ 'scanning' のままなら 'failed' へ倒す。終端済み (completed/failed/canceled) は触らない。
        try:
            conn.rollback()
            conn.execute(
                "UPDATE sources SET status = 'failed' WHERE id = ? AND status = 'scanning'",
                (source_id,),
            )
            conn.commit()
        except Exception:
            pass
        raise
    finally:
        conn.close()


# /api/sources/{source_id}/scan と /api/sources/{source_id}/files は routers/sources.py に移動済み


# /api/classification/categories は routers/compliance.py に移動済み


def _purge_chunks_for_collection(conn, col_id: str) -> None:
    """SQLite chunks/file_hashes と ChromaDB collection を一括削除する。

    fix-security-batch-v2 (2026-05-28) Sub-2D: tier ('__raw' / '__masked') の両方を
    削除するよう修正。従来は bare col_id で delete_collection を呼んでおり、
    chroma 側で実際の物理 collection 名 ({col_id}__raw / {col_id}__masked) と一致せず
    削除されずに孤立コレクションが発生していた。
    """
    conn.execute("DELETE FROM chunks WHERE collection_id = ?", (col_id,))
    conn.execute("DELETE FROM file_hashes WHERE collection_id = ?", (col_id,))
    # fix-v3 (A2-F4): parent_chunks も掃除する。従来は chunks/file_hashes/chroma のみ削除で
    # parent_chunks 行が孤立残存していた (CLAUDE.md「削除時は SQLite と ChromaDB の両方を
    # クリーンアップ」の取り残し)。publish は parent を UPSERT 上書きするが delete 経路に欠けていた。
    conn.execute("DELETE FROM parent_chunks WHERE collection_id = ?", (col_id,))
    # fix-delete-residual-pgvector: ベクター層も関係層と同一スコープで掃除する。
    # unified(pgvector) は chunks_vec が同一接続に在るため、当該 collection の __raw / __masked のみを
    # 同一トランザクションで削除する（他コレクション／本コーパスを巻き込まない＝over-purge 防止）。
    # standalone(Chroma) は従来どおり get_chroma().delete_collection（挙動不変）。
    from core.data_layer import get_data_layer as _get_data_layer
    from providers.vector_store import chroma_name_for_tier, TIER_RAW, TIER_MASKED

    if _get_data_layer().is_unified:
        for _tier in (TIER_RAW, TIER_MASKED):
            conn.execute(
                "DELETE FROM chunks_vec WHERE collection = ?",
                (chroma_name_for_tier(col_id, _tier),),
            )
    else:
        try:
            from rag import get_chroma

            _chroma = get_chroma()
            for _tier in (TIER_RAW, TIER_MASKED):
                try:
                    _chroma.delete_collection(name=chroma_name_for_tier(col_id, _tier))
                except Exception:
                    # tier 片方しか存在しないケース等は無視（idempotent）
                    pass
        except Exception:
            pass


def _purge_collections_for_workspace(conn, ws_id: str) -> None:
    """Workspaceに属する全Collectionのchunk/Chromaを掃除する。"""
    rows = conn.execute("SELECT id FROM collections WHERE workspace_id = ?", (ws_id,)).fetchall()
    for r in rows:
        _purge_chunks_for_collection(conn, r["id"])


# ─── P4-11: WS自動ポーリング・差分検出・自動Publish ───

_polling_thread = None
_polling_stop_event = threading.Event()

# Publish 同時実行制限（同時最大2スレッドまで）
_publish_semaphore = threading.Semaphore(2)


def _ws_effective_sync_config(ws_row) -> dict:
    """WSのsync_configをglobalデフォルトとマージして返す。"""
    from core.config import get_sync_config as _gsc

    g = _gsc()
    ws_cfg = {}
    raw = ws_row["sync_config"] if ws_row and "sync_config" in ws_row.keys() else None
    if raw:
        try:
            ws_cfg = json.loads(raw)
        except Exception:
            ws_cfg = {}
    return {**g, **ws_cfg}


def _detect_workspace_changes(ws_id: str) -> list[str]:
    """WSに紐づくsourcesのファイル size + 内容ハッシュ(sha256) から変更を検出し、変更があった source_id リストを返す。

    fix-folder-ingest-20260618: 旧実装は size 差のみで「同じ大きさで中身が変わった」変更を
    取りこぼしていた。size が既知と一致する場合に限り内容 sha256 を計算し、直近 publish 時に
    document_lineage へ記録された file_hash と照合して中身変更を検知する
    (未 publish でハッシュ未知のファイルは従来どおり size 判定にフォールバック)。再帰挙動は不変。
    """
    import os as _os

    conn = get_db()
    try:
        sources = conn.execute(
            "SELECT s.id, s.path FROM sources s "
            "JOIN workspace_sources ws ON s.id = ws.source_id WHERE ws.workspace_id = ?",
            (ws_id,),
        ).fetchall()
        # fix-folder-ingest-20260618: WS 単位で直近 publish 時の内容ハッシュ台帳 (file_id -> file_hash)。
        # 同 file_id が複数 publish_version を持つ場合は ASC 反復で最新 version のハッシュが残る。
        known_hashes: dict[str, str] = {}
        try:
            for hr in conn.execute(
                "SELECT file_id, file_hash FROM document_lineage "
                "WHERE workspace_id = ? ORDER BY publish_version ASC",
                (ws_id,),
            ).fetchall():
                if hr["file_hash"]:
                    known_hashes[hr["file_id"]] = hr["file_hash"]
        except Exception as _he:
            logger.warning(f"poll: WS={ws_id} lineage hash 取得失敗 (size 判定で継続): {_he}")
    finally:
        conn.close()

    def _content_changed(path: str, file_id: str) -> bool:
        """size 一致時に内容 sha256 を既知 (document_lineage) と照合。
        ハッシュ未知 or 計算失敗時は False (= size 判定に委ねる)。"""
        prev = known_hashes.get(file_id)
        if not prev:
            return False
        try:
            return compute_sha256(path) != prev
        except Exception:
            return False

    changed_sources: list[str] = []
    for s in sources:
        src_path = _os.path.abspath(_os.path.expanduser(s["path"]))
        if not _os.path.exists(src_path):
            continue
        # 既知ファイルと現状を比較（path / size / 内容ハッシュ）
        # intake-togo-v2-20260705 (Fix 7): missing=1（実体消滅を記録済み）の行は比較対象から除外。
        # 含めると消滅ファイルが毎周期「変更あり」になり自動同期が空振り再スキャンを無限反復する。
        conn2 = get_db()
        try:
            known = conn2.execute(
                "SELECT id, path, size FROM files WHERE source_id = ? AND COALESCE(missing, 0) = 0", (s["id"],)
            ).fetchall()
        finally:
            conn2.close()
        known_map = {r["path"]: r["size"] for r in known}
        known_fid = {r["path"]: r["id"] for r in known}

        current_paths: set[str] = set()
        any_changed = False
        try:
            if _os.path.isfile(src_path):
                current_paths.add(src_path)
                if known_map.get(src_path) != _os.path.getsize(src_path):
                    any_changed = True
                elif src_path in known_fid and _content_changed(src_path, known_fid[src_path]):
                    any_changed = True
            else:
                for root, _dirs, fnames in _os.walk(src_path):
                    for fn in fnames:
                        # NFD/NFC: _do_scan と同じく FS アクセスは生パス（比較先 files.path も生パス保存）
                        fp = _os.path.join(root, fn)
                        ext = _os.path.splitext(fp)[1].lower()
                        if ext not in SUPPORTED_EXTENSIONS:
                            continue
                        current_paths.add(fp)
                        try:
                            if known_map.get(fp) != _os.path.getsize(fp):
                                any_changed = True
                            elif fp in known_fid and _content_changed(fp, known_fid[fp]):
                                any_changed = True
                        except OSError:
                            continue
        except Exception as e:
            logger.warning(f"poll: WS={ws_id} src={s['id']} walk失敗: {e}")
            continue
        # 削除されたファイルがあるかも変更扱い
        if set(known_map.keys()) - current_paths:
            any_changed = True
        if any_changed:
            changed_sources.append(s["id"])
    return changed_sources


def _auto_publish_workspace(ws_id: str) -> int:
    """WS内のreadyなCollectionをすべて再Publishする。返り値は処理したCollection数。"""
    import time as _time  # fix-v301: publish 完了後の elapsed 計測用 (手動経路 server.py:2043 と同じ)
    conn = get_db()
    try:
        cols = conn.execute(
            "SELECT id FROM collections WHERE workspace_id = ? AND status IN ('ready','draft')",
            (ws_id,),
        ).fetchall()
    finally:
        conn.close()
    # P1-5: polling 経路の auto-publish でも Workspace の pdf_mode を反映する (手動 publish と同じ取得方法)
    conn_acl = get_db()
    try:
        _ws_acl = conn_acl.execute("SELECT acl_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
    finally:
        conn_acl.close()
    _pdf_mode = "fast"
    if _ws_acl and _ws_acl["acl_config"]:
        try:
            _pdf_mode = (json.loads(_ws_acl["acl_config"]) or {}).get("pdf_mode") or "fast"
        except Exception:
            _pdf_mode = "fast"
    count = 0
    for c in cols:
        col_id = c["id"]
        conn2 = get_db()
        try:
            file_rows = conn2.execute(
                "SELECT f.path FROM files f "
                "JOIN collection_files cf ON f.id = cf.file_id WHERE cf.collection_id = ?",
                (col_id,),
            ).fetchall()
            file_paths = [r["path"] for r in file_rows]
            excluded_paths = compute_exclude_paths_for_collection(conn2, col_id)
        finally:
            conn2.close()
        if not file_paths:
            continue
        # 同期版 publish 重複検知: 既に publish_jobs で running/pending な job があるならスキップ。
        # async 版 (routers/collections.py:732) と同じ条件で重複検知を行う。
        conn_dup = get_db()
        try:
            _existing = conn_dup.execute(
                "SELECT id FROM publish_jobs WHERE collection_id = ? AND status IN ('pending','running')",
                (col_id,),
            ).fetchone()
        finally:
            conn_dup.close()
        if _existing:
            logger.info(f"poll: auto-publish skip col={col_id} (publish already in progress: job={_existing['id']})")
            continue
        # Phase 2: queue 有効なら自動Publishも enqueue（worker が消費）。無効なら従来どおり inline。
        try:
            from core.job_queue import queue_enabled as _qen, enqueue as _enq

            if _qen():
                _jid = new_id()
                _cq = get_db()
                try:
                    _cq.execute(
                        "INSERT INTO publish_jobs (id, collection_id, status, total, message) VALUES (?, ?, 'pending', ?, ?)",
                        (_jid, col_id, len(file_paths), "Auto-queued (poll)"),
                    )
                    _cq.commit()
                finally:
                    _cq.close()
                _enq({"id": _jid, "type": "publish", "col_id": col_id})
                count += 1
                continue
        except Exception as _qe:
            logger.warning(f"poll: auto-publish enqueue 失敗 col={col_id}（inline にフォールバック）: {_qe}")
        try:
            conn3 = get_db()
            try:
                conn3.execute("UPDATE collections SET status = 'publishing' WHERE id = ?", (col_id,))
                conn3.commit()
            finally:
                conn3.close()
            # GUI修正(2026-05-01) #6: コレクション単位の上書き → グローバル → デフォルト
            _cs, _co = _resolve_collection_chunking(col_id)
            _t_start = _time.perf_counter()
            _final_event = None
            for _event in publish_collection_iter(
                col_id, file_paths, chunk_size=_cs, chunk_overlap=_co, excluded_paths=excluded_paths, pdf_mode=_pdf_mode
            ):
                if _event:
                    _final_event = _event
            # fix-v301: 手動 / async publish 経路 (server.py 2082-) と整合させ、完了後に
            # collection を 'ready' へ戻し publish_history / document_lineage を記録する。
            # 旧実装は generator を捨てるだけで status='publishing' のまま固着し、
            # auto-poll 同期が走った直後にそのコレクションが検索不能 (chat 不能) になっていた。
            # CLAUDE.md 設計制約「全 publish 経路で publish_history へ INSERT」漏れも同時に解消。
            if _final_event and _final_event.get("stage") == "done":
                _cc = int(_final_event.get("chunk_count", 0) or 0)
                _elapsed = _time.perf_counter() - _t_start
                conn_fin = get_db()
                try:
                    conn_fin.execute(
                        "UPDATE collections SET status = 'ready', chunk_count = ?, last_published_at = ? WHERE id = ?",
                        (_cc, datetime.now().isoformat(timespec="seconds"), col_id),
                    )
                    _log_audit(
                        conn_fin,
                        "collection_published",
                        target=col_id,
                        detail=f"Auto-published with {_cc} chunks (poll)",
                    )
                    _finalize_publish_success(conn_fin, col_id, ws_id, file_paths, _elapsed)
                    conn_fin.commit()
                finally:
                    conn_fin.close()
                # intake-togo-v2-20260705 (Fix 7): 自動同期経由でも差分スキャン結果を操作ログへ1行
                # （手動再スキャンと自動同期で挙動を分けない）。
                _adiff = {
                    "new": int(_final_event.get("new_count", 0) or 0),
                    "changed": int(_final_event.get("reingested_count", 0) or 0),
                    "skipped": int(_final_event.get("unchanged_count", 0) or 0),
                    "missing": int(_final_event.get("missing_count", 0) or 0),
                }
                _log_processing(
                    "ingest",
                    f"自動同期 完了: {_cc} チャンクを索引化（差分: 新規{_adiff['new']}・変更{_adiff['changed']}・スキップ{_adiff['skipped']}・消滅{_adiff['missing']}）",
                    level="success", job_id=f"auto-{col_id}",
                    metadata={"stage": "done", "chunk_count": _cc, "collection_id": col_id, "diff": _adiff, "auto": True},
                )
            count += 1
        except Exception as e:
            logger.warning(f"poll: auto-publish 失敗 col={col_id}: {e}")
            # fix-v3 (A2-F1): 例外時に status を 'failed' へ復旧する。従来は warning のみで
            # status='publishing' のまま固着し、当該コレクションが再起動まで検索不能だった。
            # 手動/async publish 経路 (server.py:509 / collections.py の finally safety-net) と同型。
            try:
                _cerr = get_db()
                try:
                    _cerr.execute("UPDATE collections SET status = 'failed' WHERE id = ? AND status = 'publishing'", (col_id,))
                    _log_audit(_cerr, "collection_publish_failed", target=col_id, detail=f"auto-publish failed: {e}", result="failure")
                    _cerr.commit()
                finally:
                    _cerr.close()
            except Exception:
                pass
    return count


def _polling_loop() -> None:
    """バックグラウンドで全WSをポーリングし、auto_pollなWSで変更を検出したら自動スキャン→自動Publishする。"""
    from core.config import is_feature_enabled as _is_on

    logger.info("Polling loop started")
    while not _polling_stop_event.is_set():
        try:
            if not _is_on("data_sync"):
                _polling_stop_event.wait(60)
                continue
            conn = get_db()
            try:
                ws_rows = conn.execute("SELECT id, name, sync_config FROM workspaces").fetchall()
            finally:
                conn.close()

            for ws in ws_rows:
                ws_id = ws["id"]
                ws_name = ws["name"]
                eff = _ws_effective_sync_config(ws)
                if not eff.get("auto_poll"):
                    continue
                interval = int(eff.get("poll_interval_seconds", 3600) or 3600)
                interval = max(30, min(interval, 2592000))

                # 前回のauto_scan_completeから interval 経過したか
                conn2 = get_db()
                try:
                    last = conn2.execute(
                        "SELECT timestamp FROM audit_logs WHERE action='auto_scan_complete' "
                        "AND target = ? ORDER BY timestamp DESC LIMIT 1",
                        (ws_id,),
                    ).fetchone()
                finally:
                    conn2.close()
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last["timestamp"])
                        elapsed = (datetime.now() - last_dt).total_seconds()
                        if elapsed < interval:
                            continue
                    except Exception:
                        pass

                # 変更検出
                changed = _detect_workspace_changes(ws_id)
                published = 0
                if changed:
                    # 変更があったSourceを再スキャン
                    for sid in changed:
                        try:
                            _do_scan(sid)
                        except Exception as e:
                            logger.warning(f"poll: auto-scan 失敗 src={sid}: {e}")
                    if eff.get("auto_publish", True):
                        published = _auto_publish_workspace(ws_id)
                conn3 = get_db()
                try:
                    _log_audit(
                        conn3, "auto_scan_complete", ws_id, f"changed_sources={len(changed)},published={published}"
                    )
                    conn3.commit()
                finally:
                    conn3.close()
                if changed:
                    logger.info(f"poll: WS={ws_name} 変更検出={len(changed)}件 自動Publish={published}件")
        except Exception as e:
            logger.exception(f"poll loop error: {e}")

        # 30秒ごとに WS の interval を再評価する（細かい間隔のWSにも追従）
        _polling_stop_event.wait(30)
    logger.info("Polling loop stopped")


def _start_polling_thread() -> None:
    """ポーリングスレッドを起動する（feature data_sync が有効なときのみ）。"""
    from core.config import is_feature_enabled as _is_on

    global _polling_thread
    if _polling_thread is not None and _polling_thread.is_alive():
        return
    if not _is_on("data_sync"):
        logger.info("data_sync 機能フラグOFF: polling 起動スキップ")
        return
    _polling_stop_event.clear()
    _polling_thread = threading.Thread(target=_polling_loop, name="cynovela-poll", daemon=True)
    _polling_thread.start()


def _purge_chunks_for_source(conn, source_id: str) -> None:
    """Sourceに属するfileの chunks を、関連Collectionすべてから削除する。

    DD-CYN-0115 H-3 (2026-08-15): 資料を消しても伏字済み側 (masked) のベクターが残る
    欠陥を是正した。従来はベクター層の削除に file_hashes.chunk_ids の値
    (= 伏字前側 raw の id) をそのまま両 tier へ渡していたため、伏字済み側の id は
    "{id}__masked" であって一致0件 = 伏字済みベクターが常に取り残されていた。
    Chroma 形態は rag._delete_ids_dual_tier (raw + masked の正しい変換を持つ既存関数) に
    委ねる。unified(pgvector) 形態だけは fix-delete-residual-pgvector の設計
    (関係層と同一接続・同一トランザクションで消す = 途中失敗で片肺にしない) を維持し、
    伏字済み側の id を同じ規則 ("{id}__masked") で作ってから消す。
    併せて parent_chunks の掃除を追加した (従来は関係層に親行が孤立残存していた)。

    ⚠ 巻き添え注意 (H-3 では挙動を変えない・別走行で是正): chunks / parent_chunks の
    削除鍵は source_doc = ファイル名 (basename) のため、同一 collection 内に別 source 由来の
    同名ファイルが在ると、その行まで一緒に消える。
    """
    rows = conn.execute("SELECT id, name, path FROM files WHERE source_id = ?", (source_id,)).fetchall()
    if not rows:
        return
    file_paths = [r["path"] for r in rows]
    file_names = {r["name"] for r in rows}
    placeholders = ",".join("?" for _ in rows)
    col_rows = conn.execute(
        f"SELECT DISTINCT cf.collection_id FROM collection_files cf "
        f"JOIN files f ON cf.file_id = f.id WHERE f.source_id = ?",
        (source_id,),
    ).fetchall()
    for cr in col_rows:
        col_id = cr["collection_id"]
        # SQLite chunks: source_doc がこのSourceのファイル名と一致
        # ⚠ 巻き添え注意 (H-3 §4): この鍵 (collection_id, source_doc=basename) は
        #    同名の別 source 由来ファイルの行も巻き込む。今回は挙動を変えない。
        for fname in file_names:
            conn.execute(
                "DELETE FROM chunks WHERE collection_id = ? AND source_doc = ?",
                (col_id, fname),
            )
            # DD-CYN-0115 H-3: 親 (parent_chunks) も同じ鍵で掃除する。collection 経路
            # (_purge_chunks_for_collection の DELETE FROM parent_chunks) と整合させる。
            # parent_chunks.source_doc は publish 時に child と同じ fname が入る
            # (raw 行・masked 行の両方)。FK が無いため消さないと孤立残存する。
            conn.execute(
                "DELETE FROM parent_chunks WHERE collection_id = ? AND source_doc = ?",
                (col_id, fname),
            )
        # ChromaDB: file_hashes から該当ファイルのchunk_idsを取って削除
        for fp in file_paths:
            row = conn.execute(
                "SELECT chunk_ids FROM file_hashes WHERE collection_id = ? AND file_path = ?",
                (col_id, fp),
            ).fetchone()
            if row:
                try:
                    cids = json.loads(row["chunk_ids"] or "[]")
                except Exception:
                    cids = []
                if cids:
                    # fix-security-batch-v2 (2026-05-28) Sub-2D: bare col_id ではなく tier 付きの
                    # 物理 collection 名 ({col_id}__raw / {col_id}__masked) で削除する。
                    # fix-delete-residual-pgvector: unified(pgvector) は chunks_vec を同一接続から
                    # 当該 collection × 当該 chunk_ids のみ削除（関係層 chunks と同一スコープ＝over-purge 防止）。
                    # DD-CYN-0115 H-3: tier ごとに「その層に実在する id」へ変換してから消す。
                    #   伏字前側 (raw): file_hashes.chunk_ids の値そのまま
                    #   伏字済み側 (masked): "{id}__masked" (rag._delete_ids_dual_tier と同一規則)
                    # id の形そのものには依らない (chunk_ids に入っている値をそのまま使う) ため、
                    # 旧世代の id 形 ({source}#{file}#cNNNNN:{ver}) の既存データも消せる。
                    from core.data_layer import get_data_layer as _get_data_layer
                    from providers.vector_store import chroma_name_for_tier, TIER_RAW, TIER_MASKED

                    if _get_data_layer().is_unified:
                        for _tier, _tier_ids in (
                            (TIER_RAW, [c for c in cids if c]),
                            (TIER_MASKED, [f"{c}__masked" for c in cids if c]),
                        ):
                            if not _tier_ids:
                                continue
                            _ph = ",".join("?" for _ in _tier_ids)
                            conn.execute(
                                f"DELETE FROM chunks_vec WHERE collection = ? AND id IN ({_ph})",
                                (chroma_name_for_tier(col_id, _tier), *_tier_ids),
                            )
                    else:
                        try:
                            # 変換規則を二重に持たない: rag.py の既存関数へ委ねる
                            # (raw を消し、続けて "{id}__masked" を masked 側から消す)。
                            from rag import _delete_ids_dual_tier

                            _delete_ids_dual_tier(col_id, [c for c in cids if c], label=f"source-delete:{fp}")
                        except Exception:
                            pass
                    # DD-CYN-0115 H-3: 親 (parent_chunks) を chunk id から導いた接頭辞でも掃除する。
                    # source_doc 鍵の掃除で拾えない行 (source_doc が空・食い違い等) の取りこぼし止め。
                    try:
                        from rag import _purge_parent_chunks_for_ids

                        _purge_parent_chunks_for_ids(conn, col_id, [c for c in cids if c], label=f"source-delete:{fp}")
                    except Exception:
                        pass
                conn.execute(
                    "DELETE FROM file_hashes WHERE collection_id = ? AND file_path = ?",
                    (col_id, fp),
                )


# /api/sources/{source_id} DELETE は routers/sources.py に移動済み


# workspaces CRUD/scan/sync/archive/chunks/publish-history/policy は routers/workspaces.py に移動済み
# /api/policies は別 router に移動済み


# /api/audit-logs と /api/audit-logs/export は routers/audit_logs.py に移動済み


# /api/compliance/checklist は routers/compliance.py に移動済み


def _log_processing(
    log_type: str, message: str, level: str = "info", job_id: str = "", metadata: dict | None = None
) -> None:
    """PHASE B-4: 処理ログを SQLite に記録する (publish / rag_query)。

    失敗時はサイレントに継続 (本処理を妨げない)。
    """
    try:
        import json as _json

        c = get_db()
        try:
            # rawmode-partH(additive): 取り込み(publish)ログに raw_only コレクションで
            # あることを付記する。log_type='ingest' かつ metadata.collection_id がある
            # 呼び出し(=publish系)のみ対象で、他の呼び出しには一切影響しない。
            # raw_only 列が無い旧DBや参照失敗時は try/except で従来動作のまま(完全無害化)。
            if log_type == "ingest" and metadata and metadata.get("collection_id"):
                try:
                    _ro_row = c.execute(
                        "SELECT raw_only FROM collections WHERE id = ?",
                        (metadata.get("collection_id"),),
                    ).fetchone()
                    if _ro_row is not None and int(_ro_row["raw_only"] or 0) == 1:
                        message = f"[raw_only] {message}"
                        metadata = dict(metadata)
                        metadata["raw_only"] = True
                except Exception:
                    pass
            meta_str = _json.dumps(metadata, ensure_ascii=False) if metadata else None
            c.execute(
                "INSERT INTO processing_logs (log_type, level, job_id, message, metadata_json) " "VALUES (?, ?, ?, ?, ?)",
                (log_type, level, job_id or "", message[:2000], meta_str),
            )
            c.commit()
        finally:
            c.close()
    except Exception as _e:
        # 静的: 本処理を止めない
        logger.warning(f"_log_processing 失敗: {_e}")


# /api/admin/processing-logs は routers/admin.py に移動済み


# _persist_token_usage は routers/chat.py または core/llm.py に移動済み


# _AUDIT_CATEGORY_MAP / _audit_category / _log_audit は core/audit.py に移動済み


# ─── Collections CRUD は routers/collections.py に移動済み ───


# ─── Publish API ───


def _finalize_publish_success(
    conn,
    col_id: str,
    workspace_id: str,
    file_paths: list,
    elapsed: float,
) -> dict | None:
    """Publish 成功時の共通 post-processing。

    1) document_lineage に各ファイルの sha256 / size / chunk_count を記録
    2) publish_history に WS 全体の集計 1 行を INSERT

    部分失敗（個別ファイルの open 失敗等）は warning ログだけ出して継続する。
    呼び元はこのヘルパ呼出後に明示的に conn.commit() すること。
    workspace_id が空の場合は lineage のみ実行し、history は記録せず None を返す。
    """
    # 1) document_lineage 記録
    try:
        import hashlib as _hl_lin

        file_rows_lin = conn.execute(
            """SELECT f.id, f.path, f.size FROM files f
               JOIN collection_files cf ON f.id = cf.file_id
               WHERE cf.collection_id = ?""",
            (col_id,),
        ).fetchall()
        for fr in file_rows_lin:
            fpath = fr["path"]
            if not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "rb") as _f:
                    sha = _hl_lin.sha256(_f.read()).hexdigest()
            except Exception:
                sha = ""
            ch_count = conn.execute(
                "SELECT COUNT(*) AS c FROM chunks WHERE collection_id = ? AND source_doc = ?",
                (col_id, os.path.basename(fpath)),
            ).fetchone()["c"]
            record_document_lineage(
                conn,
                file_id=fr["id"],
                workspace_id=workspace_id or "",
                collection_id=col_id,
                source_path=fpath,
                file_hash=sha,
                file_size=int(fr["size"] or 0),
                chunk_count=int(ch_count or 0),
                acl_source="cynovela",
            )
    except Exception as _e:
        logger.warning(f"document_lineage 記録失敗 (continuing): {_e}")

    # 2) publish_history 記録
    if not workspace_id:
        return None
    try:
        rows = conn.execute(
            "SELECT char_count, pii_detected, excluded FROM chunks WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchall()
        total_chunks = len(rows)
        # ga-close-v3 PartD D-3: 伏字件数は guardrail.pii_counts_from_db に集約した。
        #   旧実装は層を絞らず raw+masked の両方で pii_detected を数えていたため、
        #   要約・一覧と食い違っていた (公開済み「デモ資料一式」実測: 公開履歴 2146 =
        #   raw 2128 + masked 18。同じ資料で一覧は 2128、要約は 361)。
        pii_count = int(pii_counts_from_db(conn, workspace_id=workspace_id)["pii_chunks"])
        excluded_count = sum(1 for r in rows if r["excluded"])
        char_counts = [r["char_count"] for r in rows if r["char_count"] and r["char_count"] > 0]
        avg_chars = sum(char_counts) / len(char_counts) if char_counts else 0.0
        from datetime import datetime as _dt, timezone as _tz

        ts = _dt.now(_tz.utc).isoformat()
        doc_count = len(file_paths)
        conn.execute(
            """
            INSERT INTO publish_history
            (workspace_id, timestamp, doc_count, chunk_count, pii_count,
             excluded_count, avg_chunk_chars, elapsed_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (workspace_id, ts, doc_count, total_chunks, pii_count, excluded_count, avg_chars, elapsed),
        )
        return {
            "workspace_id": workspace_id,
            "timestamp": ts,
            "doc_count": doc_count,
            "chunk_count": total_chunks,
            "pii_count": pii_count,
            "excluded_count": excluded_count,
            "avg_chunk_chars": avg_chars,
            "elapsed_seconds": elapsed,
        }
    except Exception as _e:
        logger.warning(f"publish_history 記録失敗: {_e}")
        return None


# /api/collections/{col_id}/publish[/stream|/stop|/recover] は routers/collections.py に移動済み

# ─── Phase 0c: 非同期 Publish + Job Status API ───


def _update_publish_job(
    job_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    total: int | None = None,
    message: str | None = None,
    error: str | None = None,
    conn=None,
) -> None:
    """publish_jobs の任意フィールドを更新する。updated_at は常に現在時刻を入れる。
    conn を渡されればそれを使い、close しない（呼び出し元責任）。"""
    sets = ["updated_at = datetime('now')"]
    params: list = []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if stage is not None:
        sets.append("stage = ?")
        params.append(stage)
    if progress is not None:
        sets.append("progress = ?")
        params.append(int(progress))
    if total is not None:
        sets.append("total = ?")
        params.append(int(total))
    if message is not None:
        sets.append("message = ?")
        params.append(message)
    if error is not None:
        sets.append("error = ?")
        params.append(error)
    params.append(job_id)
    _own_conn = conn is None
    c = get_db() if _own_conn else conn
    try:
        c.execute(f"UPDATE publish_jobs SET {', '.join(sets)} WHERE id = ?", params)
        c.commit()
    finally:
        if _own_conn:
            c.close()


def _run_publish_background(
    job_id: str,
    col_id: str,
    file_paths: list,
    excluded_paths: set | None,
    chunk_size: int,
    chunk_overlap: int,
    pdf_mode: str = "fast",
) -> None:
    """別スレッドで publish_collection_iter を回し、進捗を publish_jobs に書き込む。

    FIX: 接続は per-call（必要な時に開いて commit したら閉じる）方式。
    長期間ロックを保持する単一接続は他の API 書き込みを完全にブロックして
    サーバークラッシュを誘発するため不可。WAL + busy_timeout（30s）で十分。
    更新頻度は 5 ステップごと（および開始/完了/stage変化時）に削減する。
    """
    import time as _time

    t_start = _time.perf_counter()

    # FIX: 同時 Publish 数を制限（最大 2）。 5 秒で取れなければ failed として早期失敗。
    if not _publish_semaphore.acquire(timeout=5):
        try:
            _slot_conn = get_db()
            try:
                _slot_conn.execute("UPDATE collections SET status='draft' WHERE id=?", (col_id,))
                _slot_conn.commit()
            finally:
                _slot_conn.close()
            _update_publish_job(
                job_id,
                status="failed",
                stage="error",
                message="他のPublishが多すぎます。少し待ってから再試行してください。",
                error="publish_slot_busy",
            )
        except Exception:
            pass
        return

    last_progress_update = 0
    last_message = ""  # fix-s1: message 変化でも書き込み、取り込み中の生存合図を永続化する
    # ingest-oplog(additive): 取り込み操作ログ(processing_logs, log_type='ingest')を追記専用で永続。
    #   段変化と chunking 各ファイルを節目として残す(進捗バッチ毎の冗長行は出さない)。生PII非永続=
    #   message/metadata は段名・件数・ファイル名・時刻のみ(Part1-C で本文/抽出スパン非混入を確認)。
    _oplog_last_stage = None
    _oplog_last_file_progress = -1
    try:
        # 開始: collections.status = 'publishing'
        _c = get_db()
        try:
            _c.execute("UPDATE collections SET status = 'publishing' WHERE id = ?", (col_id,))
            _c.commit()
        finally:
            _c.close()
        _update_publish_job(job_id, status="running", message="開始中...")
        _log_processing(
            "ingest", "取り込み開始", level="info", job_id=job_id,
            metadata={"stage": "start", "collection_id": col_id, "file_count": len(file_paths)},
        )

        final_event = None
        for event in publish_collection_iter(
            col_id,
            file_paths,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            excluded_paths=excluded_paths,
            pdf_mode=pdf_mode,  # P1-5: 非同期 publish でも Workspace の pdf_mode を反映
        ):
            final_event = event
            stage = event.get("stage", "")
            if stage in ("chunking", "embedding"):
                progress = int(event.get("current", 0) or 0)
                total = int(event.get("total", 0) or 0)
                # ingestviz: 進捗が変化するたび、および各段の開始時(progress==0)に更新する。
                # 旧 `progress % 5 == 0` は embedding の batch_size=16 と噛み合わず
                # (end=16,32,48,64 が全て素通り→80 まで一切書かれない)、進捗バー/右ログが
                # 117秒ほど 0/128 で固着していた。書込量は 1 publish あたり概ね数〜数十回で軽微。
                _msg = str(event.get("message", ""))
                # fix-s1: 進捗値の変化に加え、message が変わった時も書き込む（大ファイル1本の間も
                #   右ログ/メッセージが動き続け「更新されない」固着を解消。進捗値の意味は不変）。
                if progress != last_progress_update or progress == 0 or _msg != last_message:
                    _update_publish_job(
                        job_id,
                        status="running",
                        stage=stage,
                        progress=progress,
                        total=total,
                        message=_msg,
                    )
                    last_progress_update = progress
                    last_message = _msg
                # ingest-oplog(additive): 段変化 or chunking の新ファイルを節目として追記永続。
                #   embedding はバッチ毎の冗長行を出さず段開始のみ(段名・件数・ファイル名のみ=生PII非永続)。
                if stage != _oplog_last_stage or (stage == "chunking" and progress != _oplog_last_file_progress):
                    _log_processing(
                        "ingest", _msg or stage, level="info", job_id=job_id,
                        metadata={"stage": stage, "current": progress, "total": total, "collection_id": col_id},
                    )
                    _oplog_last_stage = stage
                    _oplog_last_file_progress = progress
            elif stage in ("error", "stopped"):
                break
            # done は break せず後段で扱う

        elapsed = _time.perf_counter() - t_start
        stage = (final_event or {}).get("stage")
        if stage == "done":
            # final_event は上の if 分岐で None 除外済み（stage == "done" 経路では non-None 確定）
            chunk_count = int(final_event.get("chunk_count", 0) or 0)  # pyright: ignore[reportOptionalMemberAccess]
            _c = get_db()
            try:
                _c.execute(
                    "UPDATE collections SET status = 'ready', chunk_count = ?, " "last_published_at = ? WHERE id = ?",
                    (chunk_count, datetime.now().isoformat(timespec="seconds"), col_id),
                )
                _log_audit(
                    _c,
                    "collection_published",
                    target=col_id,
                    detail=f"Published with {chunk_count} chunks (job={job_id})",
                )
                ws_row = _c.execute("SELECT workspace_id FROM collections WHERE id = ?", (col_id,)).fetchone()
                ws_id = ws_row["workspace_id"] if ws_row else ""
                _finalize_publish_success(_c, col_id, ws_id, file_paths, elapsed)
                _c.commit()
            finally:
                _c.close()
            _update_publish_job(
                job_id,
                status="completed",
                stage="done",
                progress=chunk_count,
                total=chunk_count,
                message=f"Published with {chunk_count} chunks",
            )
            # intake-togo-v2-20260705 (Fix 7): 差分スキャン結果（新規/変更/スキップ/消滅）を操作ログへ1行
            _diff = {
                "new": int(final_event.get("new_count", 0) or 0),  # pyright: ignore[reportOptionalMemberAccess]
                "changed": int(final_event.get("reingested_count", 0) or 0),  # pyright: ignore[reportOptionalMemberAccess]
                "skipped": int(final_event.get("unchanged_count", 0) or 0),  # pyright: ignore[reportOptionalMemberAccess]
                "missing": int(final_event.get("missing_count", 0) or 0),  # pyright: ignore[reportOptionalMemberAccess]
            }
            _log_processing(
                "ingest",
                f"完了: {chunk_count} チャンクを索引化（差分: 新規{_diff['new']}・変更{_diff['changed']}・スキップ{_diff['skipped']}・消滅{_diff['missing']}）",
                level="success", job_id=job_id,
                metadata={
                    "stage": "done", "chunk_count": chunk_count, "collection_id": col_id, "diff": _diff,
                    # vision-placeholder-warn-20260727: 中身が1文字も入らなかったファイル。
                    #   平文がある取り込みの瞬間にしか判定できない (索引の本文は暗号文) ため、
                    #   ここで残して publish-summary から読み出す。
                    "placeholder_only_files": list(final_event.get("placeholder_only_files") or []),  # pyright: ignore[reportOptionalMemberAccess]
                    # DD-CYN-0091 C: 飛ばしたファイルの一覧 (publish-summary が読み出す)
                    "skipped_details": list(final_event.get("skipped_details") or []),  # pyright: ignore[reportOptionalMemberAccess]
                    # DD-CYN-0115 H-5: 処理の内訳 (6工程) の累積秒 (publish-summary が読み出す)
                    "stage_seconds": dict(final_event.get("stage_seconds") or {}),  # pyright: ignore[reportOptionalMemberAccess]
                },
            )
        elif stage == "stopped":
            _c = get_db()
            try:
                _c.execute("UPDATE collections SET status = 'draft' WHERE id = ?", (col_id,))
                _log_audit(
                    _c,
                    "collection_publish_stopped",
                    target=col_id,
                    # stage == "stopped" 経路では final_event non-None 確定
                    detail=final_event.get("message", "stopped"),  # pyright: ignore[reportOptionalMemberAccess]
                    result="failure",
                )
                _c.commit()
            finally:
                _c.close()
            _update_publish_job(
                job_id,
                status="stopped",
                stage="stopped",
                # 同上、stage == "stopped" 経路では final_event non-None 確定
                message=final_event.get("message", "stopped"),  # pyright: ignore[reportOptionalMemberAccess]
            )
            _log_processing(
                "ingest", "取り込みを停止しました", level="warning", job_id=job_id,
                metadata={"stage": "stopped", "collection_id": col_id},
            )
        else:
            # stage == "error" または final_event 自体が None
            detail = (final_event or {}).get("message", "unknown error")
            _c = get_db()
            try:
                _c.execute("UPDATE collections SET status = 'failed' WHERE id = ?", (col_id,))
                _log_audit(_c, "collection_publish_failed", target=col_id, detail=detail, result="failure")
                _c.commit()
            finally:
                _c.close()
            _update_publish_job(
                job_id,
                status="failed",
                stage="error",
                message=detail,
                error=detail,
            )
            _log_processing(
                "ingest", f"取り込み失敗: {detail}", level="error", job_id=job_id,
                metadata={"stage": "error", "collection_id": col_id},
            )
    except Exception as e:
        logger.exception(f"publish failed: {e}")
        try:
            _c = get_db()
            try:
                _c.execute("UPDATE collections SET status = 'failed' WHERE id = ?", (col_id,))
                _log_audit(_c, "collection_publish_failed", target=col_id, detail=str(e), result="failure")
                _c.commit()
            finally:
                _c.close()
            _update_publish_job(
                job_id,
                status="failed",
                stage="error",
                message="内部エラーが発生しました",
                error="internal_error",
            )
            _log_processing(
                "ingest", "取り込み失敗: 内部エラーが発生しました", level="error", job_id=job_id,
                metadata={"stage": "error", "collection_id": col_id},
            )
        except Exception:
            pass
    finally:
        _publish_semaphore.release()


# /api/collections/{col_id}/publish/async は routers/collections.py に移動済み


# /api/jobs/{job_id} は routers/jobs.py に移動済み


# ─── RAG Chat API ───


# /api/chat (POST) は routers/chat.py に移動済み


# ============================================================
# P6 BLOCK-E: Multi-LLM 比較
# ============================================================

# COMPARE_MODEL_PRESETS は core/constants.py に移動済み


# ============================================================
# GUI修正2 #35: アーカイブ機能 (論理削除)
# ============================================================
# _ARCHIVABLE は core/constants.py に移動済み


# /api/archived/* は routers/archived.py に移動済み


# /api/llm/presets と /api/llm/providers は routers/llm.py に移動済み


# _build_adapter_for_preset は routers/chat.py または core/llm.py に移動済み


# /api/chat/compare (POST) は routers/chat.py に移動済み


# ─── BLOCK A-3: messages / sessions / feedback の永続化ヘルパーとAPI ───

# _ensure_session は routers/chat.py または core/llm.py に移動済み


# build_conversation_context は routers/chat.py または core/llm.py に移動済み


# _persist_chat_messages は routers/chat.py または core/llm.py に移動済み


# /api/workspaces/{workspace_id}/chat/stream (POST, SSE) は routers/chat.py に移動済み


# /api/messages/{id} と /api/messages/{id}/feedback は routers/messages.py に移動済み


# /api/sessions/{session_id} は routers/sessions.py に移動済み


# ─── P2-5: DocumentLineage (Publish 来歴 / 差分検出) ───


def record_document_lineage(
    conn,
    file_id: str,
    workspace_id: str,
    collection_id: str,
    source_path: str,
    file_hash: str,
    file_size: int,
    chunk_count: int,
    acl_source: str = "cynovela",
) -> str:
    """document_lineage に upsert する。同じ file_id があれば publish_version++。"""
    existing = conn.execute(
        "SELECT id, publish_version FROM document_lineage WHERE file_id = ?",
        (file_id,),
    ).fetchone()
    now = datetime.now().isoformat(timespec="seconds")
    if existing:
        conn.execute(
            """UPDATE document_lineage SET
                 file_hash = ?, file_size = ?, chunk_count = ?,
                 publish_version = ?, source_path = ?, collection_id = ?,
                 acl_source = ?, updated_at = ?
               WHERE file_id = ?""",
            (
                file_hash,
                file_size,
                chunk_count,
                existing["publish_version"] + 1,
                source_path,
                collection_id,
                acl_source,
                now,
                file_id,
            ),
        )
        return existing["id"]
    lineage_id = new_id()
    conn.execute(
        """INSERT INTO document_lineage
             (id, file_id, workspace_id, collection_id, source_path,
              file_hash, file_size, chunk_count, publish_version,
              acl_source, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
        (
            lineage_id,
            file_id,
            workspace_id,
            collection_id,
            source_path,
            file_hash,
            file_size,
            chunk_count,
            acl_source,
            now,
            now,
        ),
    )
    return lineage_id


def get_changed_files(conn, workspace_id: str, file_hashes: dict[str, str]) -> dict:
    """前回 Publish 時の hash と比較して new/changed/unchanged に分類する。

    Args:
        file_hashes: {source_path: sha256_hash}
    Returns:
        {"new": [path...], "changed": [path...], "unchanged": [path...]}
    """
    rows = conn.execute(
        "SELECT source_path, file_hash FROM document_lineage WHERE workspace_id = ?",
        (workspace_id,),
    ).fetchall()
    existing = {r["source_path"]: r["file_hash"] for r in rows}
    out = {"new": [], "changed": [], "unchanged": []}
    for path, h in (file_hashes or {}).items():
        if path not in existing:
            out["new"].append(path)
        elif existing[path] != h:
            out["changed"].append(path)
        else:
            out["unchanged"].append(path)
    return out


# workspaces/lineage* は routers/workspaces.py に移動済み

# ─── P2-3: フォローアップ質問チップ ───

# /api/chat/followups (POST) は routers/chat.py に移動済み


# ─── P2-2: マルチターン会話 — Session CRUD API ───

# /api/sessions (GET/POST/DELETE) と /api/sessions/{session_id}/messages は
# routers/sessions.py に移動済み


# ─── Settings API ───

# /api/feedback (POST/stats/negatives) は routers/feedback.py に移動済み


# /api/browse は routers/files.py に移動済み


# /api/folder-scan-preview は routers/files.py に移動済み


# /api/pipeline-presets は別 router に移動済み


# /api/collections/{col_id}/lock (POST/DELETE) は routers/collections.py に移動済み

# /api/auth/session-config (GET/POST) は routers/auth.py に移動済み


# /api/models は routers/models.py に移動済み


# /api/admin/storage-info, cleanup/chromadb-orphans, maintenance/vacuum, export(full/csv) は
# routers/admin.py に移動済み


# /api/settings/presets, remote-access, /api/settings (GET/PUT), /models は
# routers/settings.py に移動済み (_validate_llm_endpoint も同様)


# FEATURE 8: RAG検索ヒット件数のユーザー設定（LLM の top-k とは別物）
# _get_retrieval_n_results は routers/chat.py または core/llm.py に移動済み


# FEATURE 1: システムプロンプトのユーザー上書き機能
# FEATURE 3: 任意で role prefix を先頭に付与する
def _get_effective_system_prompt(style_role: str | None = None) -> str:
    """settings テーブルに保存されたシステムプロンプトがあればそれを返す。
    無い／空なら DEFAULT_SYSTEM_PROMPT を返す。
    style_role が指定され ROLE_PROMPT_PREFIX に該当する場合は先頭に prefix を付与する。"""
    try:
        conn = get_db()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", ("system_prompt",)).fetchone()
        finally:
            conn.close()
    except Exception:
        return apply_role_prefix(DEFAULT_SYSTEM_PROMPT, style_role)
    if row and row["value"] and row["value"].strip():
        return apply_role_prefix(row["value"], style_role)
    return apply_role_prefix(DEFAULT_SYSTEM_PROMPT, style_role)


# /api/settings/system-prompt (GET/POST) と /api/settings/test-connection は routers/settings.py に移動済み


# /api/features (GET/PATCH) は routers/features.py に移動済み


# /api/execution-config + /api/chunking-config は別 router に移動済み


# /api/mcp/config と /api/mcp/test-connection は routers/mcp.py に移動済み


# /api/policy-matrix は別 router に移動済み


# /api/compliance-report.csv は別 router に移動済み


# /api/data-catalog は別 router に移動済み


# /api/pii-detections は routers/guardrails.py に移動済み


# /api/files/{file_id}/preview は routers/files.py に移動済み


# /api/data-catalog/export は別 router に移動済み


# /api/dashboard/summary は routers/dashboard.py に移動済み


# /api/health/detailed は routers/health.py に移動済み


# /api/mode は routers/mode.py に移動済み


# /api/settings/llm (GET) は routers/settings.py に移動済み


# ============================================================
# LM Studio v1 統合: モデル一覧 + 自動ロード API
# ============================================================


def _lmstudio_endpoint_from_settings() -> str:
    """現在の LLM settings から LM Studio エンドポイントを返す。"""
    a = _adapter
    if isinstance(a, MockAdapter):
        return ""
    return getattr(a, "base_url", "http://localhost:1234") or "http://localhost:1234"


# /api/lmstudio/models は routers/lmstudio.py に移動済み


# /api/llm/context-length と /api/llm/list-models は routers/llm.py に移動済み


# /api/lmstudio/load は routers/lmstudio.py に移動済み


# _get_reranker_top_n と /api/settings/reranker (GET) は routers/settings.py に移動済み


# /api/settings/reranker (POST/test), /classifier (GET/POST), /pii-mode (GET/PUT),
# /vector-store (GET/POST), /embedding (GET/POST) は routers/settings.py に移動済み


# /api/settings/llm (POST) は routers/settings.py に移動済み


# ─── P1-4: ファイルアップロードGUI ───


# 拡張子: 既存 SUPPORTED_EXTENSIONS と同じ範囲
ALLOWED_UPLOAD_EXT = {
    ".txt",
    ".md",
    ".pdf",
    ".docx",
    ".csv",
    ".pptx",
    # PHASE M-1: マルチフォーマット
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".eml",
    ".zip",
    # PHASE M-2: 画像 (image.processing_mode で実際の挙動を切替)
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".webp",
    ".gif",
}


# /api/upload は routers/files.py に移動済み


# DataSync 設定群は routers/settings.py に移動済み
# (_data_sync_state も routers/settings.py に移動)


# ─── BLOCK C: Workspace Export / Import ───

# workspaces/export は routers/workspaces.py に移動済み

# /api/workspaces/{workspace_id}/full-export は routers/chat.py に移動済み


# /api/workspaces/import は routers/chat.py に移動済み


# ─── BLOCK B-2: ロール切替デモのシードと API ───
# ROLE_DEMO_WS_NAME は core/constants.py に移動済み
# P5-A: 新ロール（admin/sales/finance/engineer）でACL検証ができるよう、
# allowed_roles に旧3ロール + 新ロールを併記しておく（後方互換）。
ROLE_DEMO_DIRS = [
    {
        "name": "demo-sales",
        "path": "./data/demo/sales",
        "allowed_roles": ["admin", "sales"],
        "label": "Sales",
    },
    {"name": "demo-hr", "path": "./data/demo/hr", "allowed_roles": ["admin", "finance"], "label": "HR/Finance"},
    {
        "name": "demo-legal",
        "path": "./data/demo/legal",
        "allowed_roles": ["admin", "viewer", "engineer", "sales", "finance"],
        "label": "Legal",
    },
]


def _seed_role_switch_demo() -> None:
    """data/demo/{sales,hr,legal} を Source/Workspace/Collection として一括投入する。
    既に投入済み (Workspace name で判定) の場合はスキップする。"""
    if not all(os.path.isdir(d["path"]) for d in ROLE_DEMO_DIRS):
        return  # data/demo が無いので何もしない
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM workspaces WHERE name = ?", (ROLE_DEMO_WS_NAME,)).fetchone()
        if existing:
            return
        # 1) Sources
        # C-5: 登録は API と同じ正規化+重複判定ヘルパを通す (入口ごとの重複登録を防ぐ)。
        # 既に同一正規化パスの source が在ればそれを使う (seed の冪等性を保つ)。
        from routers.sources import _find_existing_source, _normalize_source_path

        sids: list[str] = []
        for d in ROLE_DEMO_DIRS:
            _np = _normalize_source_path(d["path"])
            _dup = _find_existing_source(conn, _np)
            if _dup:
                sids.append(_dup["id"])
                continue
            sid = new_id()
            conn.execute(
                "INSERT INTO sources (id, name, path, status) VALUES (?, ?, ?, 'idle')",
                (sid, d["name"], _np),
            )
            sids.append(sid)
        # 2) Workspace
        ws_id = new_id()
        conn.execute(
            "INSERT INTO workspaces (id, name) VALUES (?, ?)",
            (ws_id, ROLE_DEMO_WS_NAME),
        )
        for sid in sids:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_sources (workspace_id, source_id) VALUES (?, ?)",
                (ws_id, sid),
            )
        # 全users をリンク
        for uid in [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]:
            conn.execute(
                "INSERT OR IGNORE INTO workspace_users (workspace_id, user_id) VALUES (?, ?)",
                (ws_id, uid),
            )
        conn.commit()
    finally:
        conn.close()
    # 3) スキャン (バックグラウンド)、4) Collection 作成 + Publish
    for sid, d in zip(sids, ROLE_DEMO_DIRS):
        try:
            _do_scan(sid)
        except Exception as e:
            logger.warning(f"demo scan {sid}: {e}")
        # Collection 作成 (allowed_roles 付き)
        conn = get_db()
        try:
            files = conn.execute("SELECT id FROM files WHERE source_id = ?", (sid,)).fetchall()
            file_ids = [f["id"] for f in files]
            if not file_ids:
                continue
            cid = new_id()
            conn.execute(
                "INSERT INTO collections (id, name, workspace_id, access_level, allowed_roles_json) "
                "VALUES (?, ?, ?, 'public', ?)",
                (cid, d["name"] + "-col", ws_id, json.dumps(d["allowed_roles"])),
            )
            for fid in file_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO collection_files (collection_id, file_id) VALUES (?, ?)",
                    (cid, fid),
                )
            conn.commit()
        finally:
            conn.close()
        # Publish (同期)
        try:
            from rag import publish_collection

            file_paths = []
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT path FROM files WHERE id IN ({})".format(",".join("?" for _ in file_ids)),
                    file_ids,
                ).fetchall()
                file_paths = [r["path"] for r in rows]
            finally:
                conn.close()
            publish_collection(cid, file_paths)
            conn = get_db()
            try:
                conn.execute("UPDATE collections SET status='ready' WHERE id = ?", (cid,))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"demo publish {cid}: {e}")
    logger.info(f"BLOCK B-2: ロール切替デモを投入しました (workspace={ws_id})")


# /api/demo/role-switch は routers/demo.py に移動済み


# ─── Static Files ───

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# settings-reflect-cachebust-fix-20260628 (F1): 静的アセット応答に Cache-Control: no-cache を
# 付与し、ブラウザに毎回 etag 再検証させる（変更なし=304 / 変更あり=新JS取得）。
# `?v=` バスティング無しでも「直したのに反映されない（旧JS使い回し）」を塞ぐ。
# StaticFiles マウント(/frontend, /static)のみ対象で API ルートには影響しない。
class _NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


# / と /chat-popup は routers/pages.py に移動済み


# ─────────────────────────────────────────────────────────
# PHASE 10: Agentic RAG - /api/agent/chat
# ReAct パターン (THOUGHT/ACTION/INPUT) でマルチステップ検索を実行する
# ─────────────────────────────────────────────────────────
# /api/agent/chat は routers/agent.py に移動済み


# fix-chromapath-20260525: cwd 依存を排し、`__file__` 基準で絶対パス化する。
# `/tmp` から起動した場合に `RuntimeError: Directory 'frontend' does not exist`
# が出ていた根本原因。_SRV_APP_DIR は行 1072 で定義済み。
import os as _os_static

_FRONTEND_DIR = os.path.join(_SRV_APP_DIR, "frontend")
app.mount("/frontend", _NoCacheStaticFiles(directory=_FRONTEND_DIR), name="frontend")
# Hotfix-1: vendored 3rd-party scripts (Chart.js + plugins) for offline / CDN-free serving
_STATIC_DIR = os.path.join(_SRV_APP_DIR, "static")
if _os_static.path.isdir(_STATIC_DIR):
    app.mount("/static", _NoCacheStaticFiles(directory=_STATIC_DIR), name="static")


# ─── Main ───

# モード別必要モデル定義
_MODE_MODELS = {
    "full": [
        ("BAAI/bge-m3", "約2.3GB", "Embedding"),
        ("BAAI/bge-reranker-v2-m3", "約2.1GB", "Reranker（オプション）"),
    ],
    "text": [
        ("BAAI/bge-m3", "約2.3GB", "Embedding"),
    ],
    "lite": [
        ("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "約470MB", "Embedding"),
    ],
    "lite-en": [
        ("sentence-transformers/paraphrase-MiniLM-L3-v2", "約22MB", "Embedding（英語特化）"),
    ],
    "minimal": [],
}


def _preflight_model_check(args) -> bool:
    """起動前にモデル存在確認を行う。--mock / minimal はスキップ。

    モデルが見つからない場合はユーザーに選択肢を提示する。
    戻り値: True=起動続行 / False=起動キャンセル
    """
    mode = getattr(args, "mode", "text")

    # v3.5.0 Stage1-B: --mock 撤去。モデル不要起動は minimal (TF-IDF) のみ。
    if mode == "minimal":
        return True

    required = _MODE_MODELS.get(mode, [])
    if not required:
        return True

    from core.model_paths import resolve_model_path as _resolve

    missing = []
    for model_name, size, role in required:
        resolved = _resolve(model_name)
        # resolve_model_path はキャッシュ未ヒット時に model_name そのものを返す
        if not resolved or resolved == model_name:
            missing.append((model_name, size, role))

    if not missing:
        return True

    print("\n" + "=" * 60)
    logger.info(f"[Cynovela] 起動モード: {mode}")
    logger.warning("[Cynovela] 必要なモデルが見つかりません:")
    print("=" * 60)
    for model_name, size, role in missing:
        print(f"  [{role}] {model_name}  ({size})")
    print()

    # v3.5.0 Stage1-B: mock モード案内を撤去し、モデル不要起動は minimal (TF-IDF) を案内。
    alternatives = {
        "full": "  [2] textモードで起動する（Rerankerなし）\n  [3] minimalモードで起動する（モデル不要・TF-IDF）",
        "text": "  [2] liteモードで起動する（約470MB・多言語対応）\n  [3] lite-enモードで起動する（約22MB・英語のみ）\n  [4] minimalモードで起動する（モデル不要・TF-IDF）",
        "lite": "  [2] lite-enモードで起動する（約22MB・英語のみ）\n  [3] minimalモードで起動する（モデル不要・TF-IDF）",
        "lite-en": "  [2] minimalモードで起動する（モデル不要・TF-IDF）",
    }
    alt_text = alternatives.get(mode, "  [2] minimalモードで起動する（モデル不要・TF-IDF）")
    cancel_num = 2 + alt_text.count("\n") + 1

    # ga-mas-c8-20260729 (C-B8): 選択メニューは対話 (stdin が TTY) のときだけ表示する。
    # 非対話では [1] を選べないため、案内は下の停止メッセージに一本化する。
    _interactive = sys.stdin.isatty()
    if _interactive:
        print("  [1] 今すぐダウンロードして起動する")
        print(alt_text)
        print(f"  [{cancel_num}] キャンセル")
        print()

        if mode == "lite-en":
            print("  ※ 日本語ドキュメントには --mode lite を推奨します")
            print()

    if os.environ.get("CYNOVELA_NONINTERACTIVE") == "1":
        logger.error("[Cynovela] CYNOVELA_NONINTERACTIVE=1: モデル不在のため起動中止 (exit 2)")
        sys.exit(2)

    # ga-mas-c8-20260729 (C-B8) / DD-CYN-0115 M-11①:
    # 画面を持たない起動 (stdin 非TTY) で選択肢 [1] を自動選択し、HF_HUB_OFFLINE /
    # TRANSFORMERS_OFFLINE を一時解除して外 (huggingface.co) から取りに行く経路を除去した
    # (旧 litefix-noninteractive-dl-20260710)。非TTY では取りに行かず、足りていないモデルの
    # 名前・置き場・期待するレイアウト・入手方法を名指しして停止する
    # (確かめられないことを約束しない)。終了コードは上の CYNOVELA_NONINTERACTIVE=1 経路と
    # 同じ exit 2。遮断の一時解除は、対話起動で人が [1] を明示的に選んだときにだけ起こる。
    if not _interactive:
        _models_dir = os.path.join(
            os.environ.get("CYNOVELA_DATA_DIR")
            or os.path.join(os.path.dirname(os.path.abspath(__file__)), "store"),
            "models",
        )
        logger.error("[Cynovela] 非対話実行(stdin 非TTY)を検出: モデルの自動ダウンロードは行いません。")
        logger.error(f"[Cynovela] 起動モード {mode} に必要なモデルが手元にありません:")
        for model_name, size, role in missing:
            logger.error(f"[Cynovela]   - {model_name} (用途: {role} / 想定サイズ: {size})")
        logger.error(f"[Cynovela] 置き場所: {_models_dir}")
        for model_name, size, role in missing:
            _layout = os.path.join(_models_dir, "models--" + model_name.replace("/", "--"))
            logger.error(f"[Cynovela]   期待するレイアウト: {_layout}/snapshots/<revision>/")
        logger.error("[Cynovela] 入手方法:")
        logger.error("[Cynovela]   (1) モデル同梱の配布物 (全部入り) を使う。上記が store/models に入っています。")
        logger.error(f"[Cynovela]   (2) 外へ出られる別の端末で取得し、{_models_dir} へ写す:")
        for model_name, size, role in missing:
            logger.error(f"[Cynovela]        huggingface-cli download {model_name} --cache-dir <取得先>")
        logger.error("[Cynovela]   (3) 画面のある端末 (stdin が TTY) で起動し、メニューの [1] を選ぶ。")
        logger.error("[Cynovela]   (4) モデル不要で起動する: --mode minimal (TF-IDF)。")
        logger.error("[Cynovela] モデル不在のため起動中止 (exit 2)")
        sys.exit(2)

    try:
        answer = input("選択: ").strip()
    except (EOFError, KeyboardInterrupt):
        logger.warning("[Cynovela] キャンセルしました。")
        return False

    if answer == "1":
        import pathlib

        # litefix-noninteractive-dl-20260710: image には HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 が
        # 焼き込まれており（RAG 実行時の egress 統制）、そのままではアプリ自身のモデルDL機能まで
        # 「outgoing traffic has been disabled」で失敗する。DL 操作の間だけ解除し、finally で必ず
        # 元へ戻す（実行時のオフライン姿勢は不変）。huggingface_hub は import 時に環境変数を
        # constants へ束縛するため、env と constants の両方を上書き・復元する。
        _off_prev = {k: os.environ.get(k) for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
        os.environ["HF_HUB_OFFLINE"] = "0"
        os.environ["TRANSFORMERS_OFFLINE"] = "0"

        def _restore_offline_env():
            for _k, _v in _off_prev.items():
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v

        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            _restore_offline_env()
            logger.warning("[Cynovela] huggingface_hub が見つかりません。")
            logger.info("[Cynovela] pip install huggingface_hub でインストールしてください。")
            return False
        _hf_constants = None
        _hf_const_prev = None
        try:
            from huggingface_hub import constants as _hf_constants
            _hf_const_prev = getattr(_hf_constants, "HF_HUB_OFFLINE", None)
            _hf_constants.HF_HUB_OFFLINE = False
        except Exception:
            _hf_constants = None
        # 状態は store/ 配下に集約 (ホームに状態を置かない)。モデルDLも store/models へ。
        _models_base = os.environ.get("CYNOVELA_DATA_DIR") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "store"
        )
        save_dir = pathlib.Path(_models_base) / "models"
        save_dir.mkdir(parents=True, exist_ok=True)
        # litefix-noninteractive-dl-20260710: 配布 run.sh は store/models を read-only bind mount
        # するため、書込不可なら ~/.cynovela/models へフォールバックする。どちらも
        # resolve_model_path() の探索候補に含まれる場所である。root 実行では os.access が
        # 当てにならない（ro マウントでも W_OK が真になり得る）ため、実書込(touch)で判定する。
        try:
            _probe = save_dir / ".dt_write_probe"
            _probe.touch()
            _probe.unlink()
        except OSError:
            _fallback = pathlib.Path(os.path.expanduser("~/.cynovela/models"))
            logger.info(f"[Cynovela] {save_dir} は書込不可(read-onlyマウント等)のため {_fallback} へ保存します。")
            save_dir = _fallback
            save_dir.mkdir(parents=True, exist_ok=True)
        all_ok = True
        # berth-ga-readiness-20260923: この対話 DL 経路でも tools/download-rag-models.py と同じ
        # locks/models.json の固定リビジョンを取る (従来は revision 未指定 = main の最新)。
        # 固定表に無いモデルは従来どおり revision=None (既定ブランチ)。
        try:
            _pins = {m["model"]: m["revision"] for m in json.loads(
                (pathlib.Path(__file__).resolve().parent / "locks" / "models.json").read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError, TypeError):
            _pins = {}
        try:
            for model_name, size, role in missing:
                # litefix-noninteractive-dl-20260710: 保存を local_dir=<org>__<name>（フラット・
                # preflight の resolve_model_path も provider の探索も読まないレイアウト＝DL成功しても
                # 次回起動で再び「モデル不在」になる）から、cache_dir=save_dir（HF キャッシュ形式
                # models--<org>--<name>/snapshots/）へ変更。resolve_model_path() が次回起動から
                # そのまま発見でき、同梱配布モデル（models--BAAI--bge-m3）とも同形式になる。
                logger.info(f"[Cynovela] ダウンロード中: {model_name} ({size})")
                logger.info(f"[Cynovela] 保存先: {save_dir / ('models--' + model_name.replace('/', '--'))}")
                try:
                    if model_name in _pins:
                        logger.info(f"[Cynovela] 固定リビジョン: {_pins[model_name]}")
                    snapshot_download(
                        repo_id=model_name,
                        revision=_pins.get(model_name),
                        cache_dir=str(save_dir),
                    )
                    logger.info(f"[Cynovela] ダウンロード完了: {model_name}")
                except Exception as e:
                    logger.error(f"[Cynovela] ダウンロード失敗: {model_name}")
                    logger.error(f"[Cynovela] エラー: {e}")
                    logger.info("[Cynovela] ネットワーク接続を確認してください。")
                    all_ok = False
        finally:
            _restore_offline_env()
            if _hf_constants is not None:
                _hf_constants.HF_HUB_OFFLINE = _hf_const_prev
        if not all_ok:
            logger.error("[Cynovela] 一部のモデルのダウンロードに失敗しました。")
            # ga-mas-c8-20260729 (C-B8): この分岐へは対話起動で [1] を選んだときにしか到達しない
            # (非対話は上で exit 2 済み)。旧 _auto_download による exit 2 は前提消滅につき削除。
            logger.warning("[Cynovela] 起動をキャンセルします。")
            return False
        logger.info("[Cynovela] 全モデルの準備が完了しました。起動します。")
        return True
    elif answer == "2":
        alt_mode = {
            "full": "text",
            "text": "lite",
            "lite": "lite-en",
            "lite-en": "minimal",
        }.get(mode, "minimal")
        logger.info(f"[Cynovela] --mode {alt_mode} オプションを付けて再起動してください。")
        return False
    else:
        logger.warning("[Cynovela] 起動をキャンセルしました。")
        return False


def _wire_providers_for_mode(app_config, yaml_cfg: dict) -> None:
    """起動モードと cynovela.yaml に基づいて provider を配線する。

    Stage R4-2/3: バグ 6 の解消。Phase 3 Recon Agent H §2.6 で確認:
    旧実装は rag.reranker_enabled + reranker_url の HttpReranker 専用分岐のみで、
    cynovela.yaml:90-97 の reranker.provider (cross_encoder / flashrank / mlx / ...)
    が無視されて NoReranker 固定になっていた。

    本関数では:
    - providers.reranker.get_reranker_provider(yaml_cfg) に委譲し、
      cynovela.yaml の reranker.provider 設定に従う (v3.5.0 Stage1-B: --mock 経路は撤去)

    優先度ルール:
    1. yaml の reranker.provider (cross_encoder / http / mlx / etc.)
    2. legacy rag.reranker_enabled + reranker_url は http 経路で吸収済み
    """
    from providers.reranker import NoReranker

    # ga-finish-20260727 (Part1-3): 起動の指定 (--mode full) で再ランクの有無が決まる
    # 旧ゲート (multimodal_enabled 以外は NoReranker 固定) を撤去し、設定
    # (rag.reranker_enabled + reranker.provider/device) を正とする。
    # provider 構築は遅延ロードのためモデルのロードコストは起動時には発生しない
    # (_MODE_MODELS の定義自体は不変)。
    # berth 差異: 姉妹系統は同時に cynovela.yaml の既定を外の口 (Mac Accelerator Service)
    # へ倒したが、berth の既定は本体内 (reranker.provider=cross_encoder) のまま据え置く
    # (理由は cynovela.yaml の reranker 節のコメント参照)。したがって本ゲート撤去の実効は
    # 「--mode text 等でも rag.reranker_enabled / reranker.provider の指定どおりに再ランクが
    #  効くようになる」ことであり、既定構成では初回の再ランク時に CrossEncoder の重みが
    # 遅延ロードされる。

    # F6: rag.reranker_enabled をマスタースイッチとして尊重する。
    #     明示的に false かつ legacy reranker_url 未指定なら reranker を無効化する。
    #     （キー欠落時は後方互換で有効=True 扱い。設定と実挙動の食い違いを解消）
    _rag_yaml = yaml_cfg.get("rag") or {}
    if _rag_yaml.get("reranker_enabled", True) is False and not _rag_yaml.get("reranker_url"):
        set_reranker_provider(NoReranker())
        logger.info("[Cynovela] Reranker: NoReranker (rag.reranker_enabled=false)")
        return

    # legacy 互換: rag.reranker_enabled + reranker_url が指定されている場合は http へ転写
    if _rag_yaml.get("reranker_enabled") and _rag_yaml.get("reranker_url"):
        # reranker セクションがなければ legacy 設定を反映
        _reranker_yaml = yaml_cfg.setdefault("reranker", {})
        if not _reranker_yaml.get("provider") or _reranker_yaml.get("provider") == "none":
            _reranker_yaml["provider"] = "http"
            _reranker_yaml["base_url"] = _rag_yaml["reranker_url"]

    try:
        provider = get_reranker_provider(yaml_cfg)
        set_reranker_provider(provider)
        # ga-finish-20260727: 実行場所 (外の口 / 本体内) がログから読めるように base_url も出す
        _rr_ep = getattr(provider, "base_url", "") or ""
        logger.info(
            f"[Cynovela] Reranker: {type(provider).__name__}" + (f" (endpoint={_rr_ep})" if _rr_ep else "")
        )
    except Exception as _e:
        logger.warning(f"[Cynovela] Reranker init failed: {_e} (NoReranker fallback)")
        set_reranker_provider(NoReranker())


# ─── routers 登録 ────────────────────────────────────────────────
from routers import health as _r_health
from routers import auth as _r_auth
from routers import sessions as _r_sessions
from routers import users as _r_users
from routers import models as _r_models
from routers import mode as _r_mode
from routers import lmstudio as _r_lmstudio
from routers import demo as _r_demo
from routers import jobs as _r_jobs
from routers import feedback as _r_feedback
from routers import stats as _r_stats
from routers import dashboard as _r_dashboard
from routers import alerts as _r_alerts
from routers import cost as _r_cost
from routers import reports as _r_reports
from routers import audit_logs as _r_audit_logs
from routers import mcp as _r_mcp
from routers import mcp_http as _r_mcp_http  # DD-CYN-0132 Phase 6: HTTP MCP 待受
from routers import features as _r_features
from routers import archived as _r_archived
from routers import llm as _r_llm
from routers import agent as _r_agent
from routers import sources as _r_sources
from routers import admin as _r_admin
from routers import collections as _r_collections
from routers import keys as _r_keys  # DD-CYN-0132 4-2: 用途限定の鍵
from routers import workspaces as _r_workspaces
from routers import policies as _r_policies
from routers import pipeline_config as _r_pipeline_config
from routers import catalog as _r_catalog
from routers import pages as _r_pages
from routers import files as _r_files
from routers import messages as _r_messages
from routers import compliance as _r_compliance
from routers import guardrails as _r_guardrails
from routers import settings as _r_settings
from routers import chat as _r_chat
# SECURITY(bugaudit-20260706): /api/transcribe は生の文字起こしを伏字なしで返し、
#   認証済みなら viewer でも音声内の生PIIを平文取得できる統治ホールだった。マウントしない。
# from routers import transcribe as _r_transcribe
# DD-CYN-0020 U-2: 音声入力そのものを撤去した。統治経路だった routers/voice.py も
#   ファイルごと削除したため、ここでの取り込みもやめる。音声の受け口は 1 つも無い。

app.include_router(_r_health.router)
app.include_router(_r_auth.router)
app.include_router(_r_sessions.router)
app.include_router(_r_users.router)
app.include_router(_r_models.router)
app.include_router(_r_mode.router)
app.include_router(_r_lmstudio.router)
app.include_router(_r_demo.router)
app.include_router(_r_jobs.router)
app.include_router(_r_feedback.router)
app.include_router(_r_stats.router)
app.include_router(_r_dashboard.router)
app.include_router(_r_alerts.router)
app.include_router(_r_cost.router)
app.include_router(_r_reports.router)
app.include_router(_r_audit_logs.router)
app.include_router(_r_mcp.router)
app.include_router(_r_mcp_http.router)  # DD-CYN-0132 Phase 6: HTTP MCP 待受
app.include_router(_r_features.router)
app.include_router(_r_archived.router)
app.include_router(_r_llm.router)
app.include_router(_r_agent.router)
app.include_router(_r_sources.router)
app.include_router(_r_admin.router)
app.include_router(_r_collections.router)
app.include_router(_r_keys.router)  # DD-CYN-0132 4-2
app.include_router(_r_workspaces.router)
app.include_router(_r_policies.router)
app.include_router(_r_pipeline_config.router)
app.include_router(_r_catalog.router)
app.include_router(_r_pages.router)
app.include_router(_r_files.router)
app.include_router(_r_messages.router)
app.include_router(_r_compliance.router)
app.include_router(_r_guardrails.router)
app.include_router(_r_settings.router)
app.include_router(_r_chat.router)
# app.include_router(_r_transcribe.router)  # SECURITY(bugaudit-20260706): 生PII egress ホール封鎖のため撤去
# DD-CYN-0020 U-2: app.include_router(_r_voice.router) は音声撤去に伴い削除。


# ─── OpenAPI 文書化補完: 認証ゲート由来の 401/403 をグローバルに注入 ───
# `core/auth.py` の `_require_*` ヘルパは FastAPI の Depends() ではなく
# EP 関数の冒頭で手で呼ぶ方式のため、FastAPI の OpenAPI 自動生成が 401/403 を
# `responses` に載せられない（Schemathesis で「Undocumented HTTP status code: 401」
# が 176 件検出される根因）。
# 実挙動（リクエスト処理・認証・レスポンス）は変えず、生成スキーマだけ補完する。
def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    from fastapi.openapi.utils import get_openapi as _get_openapi
    from core.auth import PUBLIC_PATHS as _PUB
    schema = _get_openapi(
        title=app.title,
        version=getattr(app, "version", "0.1.0") or "0.1.0",
        routes=app.routes,
    )
    for path, path_item in (schema.get("paths") or {}).items():
        if path in _PUB:
            continue
        if not isinstance(path_item, dict):
            continue
        for _method, op in path_item.items():
            if _method.startswith("x-") or not isinstance(op, dict):
                continue
            responses = op.setdefault("responses", {})
            responses.setdefault("401", {"description": "Unauthorized"})
            responses.setdefault("403", {"description": "Forbidden"})
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Cynovela server")
    # DD-CYN-0115 M-11②: 旧文言は「再起動ごとにリセット」と案内していたが、--demo の
    #   データベースは store/db/demo.db という普通のファイルで、起動し直しても消えない
    #   (消す処理はコードのどこにも無い)。起こらないことを案内しない。
    parser.add_argument("--demo", action="store_true", help="同梱のダミー資料が載ったデモのデータベース(store/db/demo.db)で起動（付けなければ本番＝空のデータベース。デモのデータベースは再起動しても消えません）")
    # v3.5.0 Stage1-B: --mock 起動コード経路を完全撤去。モデル不要起動は --mode minimal (TF-IDF) を使う。
    parser.add_argument("--lmstudio-url", default="http://localhost:1234", help="LM Studio のベースURL")
    # P4-4: PII検出モードは cynovela.yaml の pii_mode キーで管理（CLI引数は廃止）
    # BLOCK A-1: 起動モード4種
    parser.add_argument(
        "--mode",
        choices=["full", "text", "lite", "lite-en", "minimal", "worker"],
        default="text",
        help=(
            "full: マルチモーダル + Reranker 含む全機能（Apple Silicon / Windows GPU推奨）／"
            "text: テキストRAG全機能（GPU不要・既定）／"
            "lite: 軽量Embedding への切替は未配線＝現状は text と同じ bge-m3 で動作（PII有効）／"
            "lite-en: 英語特化軽量への切替は未配線＝現状は bge-m3 で動作（PII有効）／"
            "minimal: TF-IDF は未統合＝現状も bge-m3 が必須（モデル未配置環境では取り込み不可・PII有効）／"
            "worker: Phase2 ジョブ消費 worker（uvicorn を起動せず Redis キューを消費。"
            "埋め込みティアは yaml worker.embedding_mode 既定 text）"
        ),
    )
    # Fix-6: デフォルトは loopback のみ。LAN 公開は --lan を明示する必要がある。
    parser.add_argument("--host", default="127.0.0.1", help="バインドアドレス（default: 127.0.0.1）")
    parser.add_argument("--lan", action="store_true", help="LAN 公開（host=0.0.0.0 を明示）")
    parser.add_argument("--port", type=int, default=8765, help="ポート番号（default: 8765）")
    parser.add_argument(
        "--allow-tailscale", action="store_true", help="TailScale サブネット (100.64.0.0/10) からのアクセスを許可"
    )
    parser.add_argument(
        "--allow-subnet", action="append", default=[], help="追加で許可するサブネット (例: 192.168.0.0/16)。複数指定可"
    )
    parser.add_argument(
        "--reset-admin",
        action="store_true",
        default=False,
        help="Reset the admin password, print a new one, and exit",
    )
    args = parser.parse_args()

    # Phase 2: --mode worker は「ロール指定」（Redis キュー消費）。埋め込みティアは uvicorn 経路と
    # 同じく provider 配線が必要なため、yaml worker.embedding_mode（既定 text）へ解決し、
    # 以降の起動シーケンス（preflight / AppConfig / _wire_providers_for_mode / pii_mode / secret.key）
    # を通常モードと同一に通す。最後に uvicorn の代わりに worker ループへ分岐する（下部参照）。
    _worker_role = (args.mode == "worker")
    if _worker_role:
        try:
            from core.config import CYNOVELA_CONFIG as _dtc_worker

            args.mode = ((_dtc_worker.get("worker") or {}).get("embedding_mode") or "text")
        except Exception:
            args.mode = "text"
        print(f"[Cynovela] worker ロールで起動します（embedding tier={args.mode}）")

    # fixall-B5 20260602: 起動 port を環境へ公開し、routers/mcp.py 等が実ポートを参照できるようにする
    # (旧 mcp.py はポート 8765 をリテラル直書きしており --port 変更時に不整合だった)。
    os.environ["CYNOVELA_PORT"] = str(args.port)

    # Batch-B S1-1: --reset-admin で admin パスワードをリセットして終了
    if args.reset_admin:
        import sys as _sys
        import secrets as _sec_reset
        # マイグレーションを先行実行（must_change_password カラム追加保証）
        init_db()
        _new_pw = _sec_reset.token_urlsafe(16)
        _conn = get_db()
        try:
            _conn.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 1"
                " WHERE role = 'admin' AND COALESCE(is_active, 1) = 1",
                (hash_password(_new_pw),),
            )
            _conn.commit()
        finally:
            _conn.close()
        print("=" * 50)
        print("[reset-admin] Admin password has been reset.")
        print(f"[reset-admin] New password: {_new_pw}")
        print("[reset-admin] You will be required to change it on next login.")
        print("=" * 50)
        _sys.exit(0)

    # Preflight: 必要なモデルが存在しなければユーザーに確認
    if not _preflight_model_check(args):
        sys.exit(0)

    # LLM 経路固定: 非 mock 時は cynovela.yaml の llm セクション (provider/base_url/model) を正にして
    # adapter を構築する。旧実装は args.lmstudio_url (既定 http://localhost:1234) 固定で、yaml の
    # provider 指定を __main__ 起動時に上書きし state.adapter を LM Studio へ向けていた (LLM 固定漏れ)。
    try:
        from core.config import CYNOVELA_CONFIG as _DTC_llm

        _lc = (_DTC_llm.get("llm", {}) or {})
    except Exception:
        _lc = {}
    _adapter = get_llm_adapter(
        base_url=_lc.get("base_url") or args.lmstudio_url,
        provider=_lc.get("provider", "lmstudio"),
        model=_lc.get("model", ""),
        api_key=_lc.get("api_key", ""),
    )
    # _adapter は引き続き globals に残す（他のモジュールが直接参照しているため。完全廃止は別Phaseで）
    globals()["_adapter"] = _adapter

    # state.config / state.adapter を初期化（起動時の 1 回のみ）
    # v3.5.0 Stage1-B: --mock 撤去につき mock は常に False。
    _state.config = _state.AppConfig(
        demo=args.demo,
        mock=False,
        host=args.host,
        port=args.port,
        lan=args.lan,
        allow_tailscale=args.allow_tailscale,
        allow_subnet=args.allow_subnet or [],
        lmstudio_url=args.lmstudio_url,
        mode=args.mode,
    )
    _state.adapter = _adapter

    # Stage R4-3: Reranker 配線は _wire_providers_for_mode() に集約。
    # v3.5.0 Stage1-B: --mock 撤去。モデル不要の TF-IDF 埋め込みは --mode minimal で配線される。

    # BLOCK A-1: AppConfig 構築（モジュール変数として公開）
    from core.app_config import AppConfig

    _app_config = AppConfig(mode=args.mode, demo=args.demo, mock=False)
    globals()["_app_config"] = _app_config
    _state.app_config_obj = _app_config

    # P1-1: cynovela.yaml を独立ローダーで読み込み (CircuitBreaker / UI 用)
    from core.config import load_yaml_config

    yaml_cfg = load_yaml_config()
    logger.info(f"[Cynovela] cynovela.yaml loaded: {bool(yaml_cfg)}")

    # P1-2: CircuitBreaker をモジュール変数として初期化
    from providers.circuit_breaker import CircuitBreaker

    _cb_cfg = yaml_cfg.get("circuit_breaker", {}) or {}
    _llm_circuit_breaker = CircuitBreaker(
        service_name="LM Studio",
        failure_threshold=int(_cb_cfg.get("failure_threshold", 3)),
        recovery_timeout=float(_cb_cfg.get("recovery_timeout_seconds", 30.0)),
        enabled=bool(_cb_cfg.get("enabled", True)),
    )
    globals()["_llm_circuit_breaker"] = _llm_circuit_breaker
    _state.llm_circuit_breaker = _llm_circuit_breaker
    print(
        f"[Cynovela] CircuitBreaker: enabled={_llm_circuit_breaker.enabled}, "
        f"threshold={_llm_circuit_breaker.failure_threshold}, "
        f"recovery={_llm_circuit_breaker.recovery_timeout}s"
    )

    # P1-3: LLM 同時実行数を Semaphore で制限
    _max_concurrent = int((yaml_cfg.get("llm") or {}).get("max_concurrent", 3))
    import asyncio as _asyncio_init

    _llm_semaphore = _asyncio_init.Semaphore(_max_concurrent)
    globals()["_llm_semaphore"] = _llm_semaphore
    _state.llm_semaphore = _llm_semaphore
    logger.info(f"[Cynovela] LLM Semaphore: max_concurrent={_max_concurrent}")

    # P3-1: EventBus 初期化 (起動時に 1 度だけ register_all_listeners を呼ぶ)
    try:
        from services.event_bus import event_bus as _eb
        from services.listeners import register_all_listeners as _reg

        _registered_listeners = _reg(get_db, yaml_cfg)
        globals()["_event_bus"] = _eb
        _state.event_bus = _eb
        _state.registered_listeners = _registered_listeners
        logger.info(f"[Cynovela] EventBus: {_eb.listener_count()} listener(s) (wildcard 含む)")
    except Exception as _e:
        print(f"[WARN] EventBus init failed (continuing): {_e}")

    # Stage R4-2/3: 起動時 provider 配線を _wire_providers_for_mode() に集約
    # （旧実装: rag.reranker_enabled + reranker_url の HttpReranker 専用分岐のみで、
    #   cynovela.yaml:90-97 の reranker.provider=cross_encoder などが無視されていた = バグ 6）
    _wire_providers_for_mode(_app_config, yaml_cfg)

    # P4-4: PII検出モードを cynovela.yaml の pii_mode キーから読む
    try:
        from utils.metadata.pii import set_pii_detection_mode

        _pii_mode_yaml = (yaml_cfg.get("pii_mode") or "standard").strip()
        if _pii_mode_yaml not in ("lite", "standard", "quality"):
            _pii_mode_yaml = "standard"
        set_pii_detection_mode(_pii_mode_yaml)
        logger.info(f"[Cynovela] PII detection mode: {_pii_mode_yaml} (from cynovela.yaml)")
    except Exception as _e:
        logger.warning(f"[Cynovela] set_pii_detection_mode failed: {_e}")

    init_db(demo=args.demo)

    # v3.5.0 Stage1-B: --mock 撤去に伴い、--demo --mock 限定の合成データ自動投入経路も撤去。

    # BLOCK 4: 起動時のステール状態修復とPRAGMA確認
    _conn_boot = get_db()
    try:
        _conn_boot.execute("PRAGMA journal_mode = WAL")
        _conn_boot.execute("PRAGMA foreign_keys = ON")
        stale = _conn_boot.execute("SELECT id FROM collections WHERE status='publishing'").fetchall()
        if stale:
            _conn_boot.execute("UPDATE collections SET status='interrupted' WHERE status='publishing'")
            _conn_boot.commit()
            logger.info(f"[Cynovela] {len(stale)}件のpublishing中Collectionをinterruptedにリセットしました")
    finally:
        _conn_boot.close()

    legacy_mode = "demo" if args.demo else "production"
    print(f"Cynovela 起動 (mode={legacy_mode}, demo={args.demo})")
    # alpha §9-A-8: 解決後の実 DB / Chroma パスを表示する (env と解決結果が一致しているかを目視確認できる)
    _mode_label = "DEMO" if args.demo else "production"
    print(f"Mode: {_mode_label}")
    from db import DB_PATH as _RESOLVED_DB
    try:
        from rag import CHROMA_PATH as _RESOLVED_CHROMA
    except Exception:
        _RESOLVED_CHROMA = "(unresolved)"
    print(f"DB (resolved): {_RESOLVED_DB}")
    print(f"Chroma (resolved): {_RESOLVED_CHROMA}")
    # BLOCK A-1: 起動モード詳細ログ
    logger.info(f"[Cynovela] Mode: {_app_config.mode}")
    logger.info(f"[Cynovela] Demo: {_app_config.demo}")
    # 注記: lite/lite-en/minimal の軽量Embedding・TF-IDF 切替は未配線（create_embedding_provider は
    # 起動経路から未呼出）。実際の埋め込みは cynovela.yaml 既定の BAAI/bge-m3 で動作する。
    _nominal_embed = _app_config.embedding_model_name
    if _nominal_embed == "BAAI/bge-m3":
        logger.info(f"[Cynovela] Embedding: {_nominal_embed}")
    else:
        logger.info(
            f"[Cynovela] Embedding: BAAI/bge-m3（実配線）｜--mode {_app_config.mode} の名目値 "
            f"{_nominal_embed} は未配線"
        )
    logger.info(f"[Cynovela] Multimodal: {_app_config.multimodal_enabled}")
    logger.info(f"[Cynovela] PyTorch required: {_app_config.pytorch_required}")
    if _app_config.use_tfidf:
        print(
            "[Cynovela] ⚠️  minimal モード: rag.py への TF-IDF 統合は A-2 で実装予定。"
            "現状の Publish は既定の Embedding (text モード相当) で動作します。"
        )

    # P4-15: DB settings から features / exec 上書きを再ロード
    try:
        from core.config import load_runtime_overrides_from_db as _load_ov

        _load_ov(get_db)
    except Exception as _e:
        print(f"[WARN] runtime overrides の読込に失敗しました: {_e}")

    # P4-11: バックグラウンドポーリングスレッド起動
    # Phase 2: worker ロールでは起動しない（自動同期/auto-publish の起動点は API プロセスが担い、
    # worker は enqueue されたジョブの消費に専念する。二重起動による二重 publish を避ける）。
    if not _worker_role:
        try:
            _start_polling_thread()
        except Exception as _e:
            print(f"[WARN] polling thread の起動に失敗しました: {_e}")

    # P4-3: features / execution / sync 設定の起動時ログ
    try:
        from core.config import get_features as _gf, get_execution_config as _gec, get_sync_config as _gsc

        _features = _gf()
        _exec = _gec()
        _sync = _gsc()
        logger.info(f"[Cynovela] Features: " + ", ".join(f"{k}={'ON' if v else 'OFF'}" for k, v in _features.items()))
        logger.info(f"[Cynovela] AutoPoll: {_sync['auto_poll']} | " f"Interval: {_sync['poll_interval_seconds']}s")
    except Exception as _e:
        print(f"[WARN] feature/exec/sync log failed: {_e}")

    # PHASE B-1: アローリストの組み立て
    import ipaddress as _ipaddress

    _subnets: list = []
    if args.allow_tailscale:
        _subnets.append(_ipaddress.ip_network("100.64.0.0/10"))
        _ts_ip = _detect_tailscale_ip()
        if _ts_ip:
            logger.info(f"[Cynovela] TailScale IP detected: {_ts_ip}")
        else:
            logger.info("[Cynovela] WARN: --allow-tailscale 指定だが tailscale CLI から IP を取得できませんでした")
    for sn in args.allow_subnet or []:
        try:
            _subnets.append(_ipaddress.ip_network(sn))
        except ValueError as _e:
            logger.info(f"[Cynovela] WARN: --allow-subnet {sn} は不正: {_e}")
    if _subnets:
        globals()["_allowed_subnets"] = _subnets
        _state.allowed_subnets = _subnets
        logger.info(f"[Cynovela] IP allowlist: {[str(s) for s in _subnets]} (loopback は常に許可)")

    # Fix-6: --lan で 0.0.0.0 を明示。--host が直接指定されている場合はそれを優先。
    bind_host = args.host
    if args.lan and bind_host == "127.0.0.1":
        bind_host = "0.0.0.0"
    if bind_host == "0.0.0.0":
        print("WARNING: Server is listening on 0.0.0.0 and may be accessible " "from your local network.")
    print(f"http://127.0.0.1:{args.port}")
    # 状態は store/ 配下に集約 (ホームに状態を置かない)。CYNOVELA_DATA_DIR は起動時に
    # cynovela.yaml paths: から store へ解決済み (未設定時は repo 同梱 store)。
    _pid_dir = os.environ.get("CYNOVELA_DATA_DIR") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "store"
    )
    _pid_file = os.path.join(_pid_dir, "server.pid")
    os.makedirs(os.path.dirname(_pid_file), exist_ok=True)
    with open(_pid_file, "w") as _pf:
        _pf.write(str(os.getpid()))
    try:
        if _worker_role:
            # Phase 2: uvicorn は起動せず、Redis キュー消費 worker ループを回す。
            # provider 配線 / DB / secret.key / pii_mode は上の起動シーケンスで初期化済み。
            from core.worker import run_worker_loop

            print("[Cynovela] worker mode: Redis キュー消費ループを起動します（HTTP サーバは起動しません）")
            run_worker_loop()
        else:
            uvicorn.run(app, host=bind_host, port=args.port)
    finally:
        if os.path.exists(_pid_file):
            os.remove(_pid_file)
