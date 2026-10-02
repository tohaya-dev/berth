"""Cynovela — 設定・暗号化管理。

`CYNOVELA_SECRET_KEY` 環境変数に Fernet 鍵を入れておくと、
APIキー等の秘匿情報を `encrypt()` / `decrypt()` で往復できる。
鍵が未設定の場合は起動ごとに新しい鍵を生成する（開発用フォールバック）。

通行証（JWT）の署名鍵は上記の金庫鍵とは別実体で、`_JWT_SIGNING_KEY` に解決する
（part6-20260726 の二役分離。`_load_or_create_jwt_signing_key()` を参照）。
"""

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from cryptography.fernet import Fernet

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False

# distclean 20260630: cwd .env の自動読込を無効化（旧 Phase 2 autoload を撤去）。
# 旧実装は起動ディレクトリの .env を override=False で読み込んでいたため、配布先（会社PC 等
# スタンドアロン機）に残置された .env が --demo の CYNOVELA_DB 等を黙って上書きし、別DBへ
# 接続する事故源になっていた。設定は server.py の os.environ.setdefault 既定／本ファイルの
# secret.key 永続ファイル／core/llm.py の .containerenv 判定で供給され、cwd .env に依存しない。
# 明示パス指定の .env が必要な場合は別途運用で対応する（新規環境変数は追加しない方針）。


def _load_or_create_secret_key() -> str:
    """CYNOVELA_SECRET_KEY の解決順序:
    1. env CYNOVELA_SECRET_KEY (本番想定・最優先)
    2. 永続化ファイル ~/.cynovela-alphaga/secret.key (砂場・local) を読み出し
    3. (1)(2) いずれも無ければ新規生成して (2) のパスに書き込み + chmod 600

    sec4 v4.1 項目④: 起動毎再生成 WARN を解消し再起動で鍵不変にする。
    OS キーチェーンへの本格的退避は本番運用時の別作業 (SECURITY.md 参照)。
    """
    env_key = os.environ.get("CYNOVELA_SECRET_KEY")
    if env_key:
        return env_key
    key_path = Path(os.environ.get("CYNOVELA_DATA_DIR",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "store"))
               ) / "secret.key"
    if key_path.exists():
        try:
            return key_path.read_text().strip()
        except Exception as _e:
            print(f"[WARN] CYNOVELA_SECRET_KEY 永続化ファイル読込失敗 ({key_path}): {_e}")
    # 新規生成 + 永続化 + chmod 600
    new_key = Fernet.generate_key().decode()
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_key)
        os.chmod(key_path, 0o600)
        print(f"[INFO] CYNOVELA_SECRET_KEY を新規生成し永続化 (mode 600): {key_path}")
    except Exception as _e:
        print(f"[WARN] CYNOVELA_SECRET_KEY 永続化失敗 ({_e})。起動ごとに鍵が変わります: {new_key[:8]}...")
    return new_key


def _load_or_create_jwt_signing_key() -> str:
    """通行証 (JWT) の署名鍵。金庫 (Fernet 暗号化) の鍵とは別の実体を持たせる。

    二役分離 (part6-20260726): 2026-07-05 (hansolo 3616e2e) は署名鍵の公知フォールバックを
    塞ぐため署名鍵を金庫鍵へ寄せた (一鍵二役)。本関数はその際に残件化された「二役の分離」で、
    署名鍵だけを別実体へ切り出す。金庫鍵の解決・生成 (_load_or_create_secret_key) には
    一切手を入れない。移行データは無い (通行証は有効期間 8 時間の使い捨て)。

    解決順序 (**新しい環境変数は 1 つも追加しない**。既存の
    CYNOVELA_SECRET_KEY / CYNOVELA_DATA_DIR のみを使い、順序も金庫鍵の
    _load_or_create_secret_key (env → 永続ファイル → 新規生成) に揃える):

      1. env CYNOVELA_SECRET_KEY が供給されている場合は、その鍵素材から
         HMAC-SHA256 で署名鍵を「導出」する (金庫鍵そのものは使わない)。
      2. 永続化ファイル <CYNOVELA_DATA_DIR>/db/jwt/secret.key を読み出す。
      3. 無ければ暗号乱数 (secrets) で新規生成し、同パスへ書き込み + chmod 600。

    (1) が falcon 版 (d02eb24) との差異で、berth の複数ノード形態への適合である。
    falcon は無条件に (2)(3) だけを行うが、berth の K8s 形態
    (deploy/k8s/phase4a/20-api-deployment.yaml) は **replicas: 2 を 2 ノードへ分散し、
    /app/store/db を共有しない (関係層=Postgres でステートレス。config/models(ro)/uploads
    のみマウント)**。そこで falcon 版をそのまま入れると Pod ごとに別々の乱数署名鍵が
    コンテナ書込層へ生成され、Pod A が発行した通行証を Pod B が拒否する
    (Service ラウンドロビン下で認証が壊れる)。同 Deployment は金庫鍵を Secret
    cynovela-secret から env CYNOVELA_SECRET_KEY で全 Pod へ配っているため、
    そこから決定的に導出すれば全レプリカで署名鍵が一致し、かつ署名鍵の値は
    金庫鍵とは別物になる (HMAC は一方向なので、署名鍵が漏れても金庫鍵は復元できない
    = 二役分離の目的は満たす)。単一レプリカ + PVC 形態
    (deploy/k8s/20-deployment.yaml は replicas: 1 で /app/store/db に PVC cynovela-data を
    マウント・env 未設定) では (2)(3) に落ち、falcon と同じファイル方式で永続する。

    公知の推測可能な固定文字列へ落ちる経路は持たない (2026-07-05 に撤去した穴を再生産しない)。
    書き込みに失敗した場合はプロセス内限りの乱数鍵を返す (金庫鍵と同じ作り)。この場合は起動ごとに
    署名鍵が変わるため発行済みの通行証は無効になるが、再ログインで通る (金庫の中身には影響しない)。

    置き場を db/ 配下の jwt/ にする理由:
      - コンテナ / K8s では /app/store/db が書き込み可能な永続ボリューム
        (podman 名前つきボリューム cyn-db・K8s PVC cynovela-data) で、金庫鍵の
        読み取り専用 bind と衝突せず、コンテナ作り直しでも鍵が残る。
      - ファイル名を secret.key に揃えることで、既存の鍵衛生規則
        (.containerignore の `**/secret.key`) がそのまま効き、イメージレイヤや
        ビルドコンテキストへ鍵の実体が焼き込まれない。
    """
    env_key = os.environ.get("CYNOVELA_SECRET_KEY")
    if env_key:
        # 導出: 署名鍵 = HMAC-SHA256(金庫鍵素材, 用途ラベル)。金庫鍵そのものは返さない。
        return hmac.new(
            env_key.encode() if isinstance(env_key, str) else env_key,
            b"cynovela-jwt-signing-v1",
            hashlib.sha256,
        ).hexdigest()
    key_path = Path(os.environ.get("CYNOVELA_DATA_DIR",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "store"))
               ) / "db" / "jwt" / "secret.key"
    if key_path.exists():
        try:
            existing = key_path.read_text().strip()
            if existing:
                return existing
        except Exception as _e:
            print(f"[WARN] JWT 署名鍵の永続化ファイル読込失敗 ({key_path}): {_e}")
    new_key = secrets.token_urlsafe(64)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_text(new_key)
        os.chmod(key_path, 0o600)
        print(f"[INFO] JWT 署名鍵を新規生成し永続化 (mode 600): {key_path}")
    except Exception as _e:
        print(f"[WARN] JWT 署名鍵の永続化失敗 ({_e})。起動ごとに署名鍵が変わります "
              f"(発行済みの通行証は無効・再ログインで再発行されます)")
    return new_key


_KEY = _load_or_create_secret_key()
_fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)

# 通行証の署名鍵。金庫鍵 (_KEY) とは別実体。core/auth.py の _get_jwt_secret() が消費する。
_JWT_SIGNING_KEY = _load_or_create_jwt_signing_key()


def encrypt(plaintext: str) -> str:
    """平文を Fernet で暗号化し、保存時は 'enc:' プレフィックス付き文字列にする慣習で呼出側が
    冪等を担保する (sec4 v4.1 項目⑤/⑤B)。本関数は raw Fernet 暗号文 (str) を返すのみ。
    """
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Fernet 復号。'enc:' プレフィックスの剥がしは呼出側で行う想定 (項目⑤/⑤B)。"""
    return _fernet.decrypt(ciphertext.encode()).decode()


# ─── Phase 1: cynovela.yaml 読み込み ───


def load_cynovela_config(yaml_path: str = "cynovela.yaml") -> dict:
    """cynovela.yamlを読み込んで設定辞書を返す。

    ファイルが存在しない場合はデフォルト値を返す。
    環境変数 CYNOVELA_* でオーバーライド可能。
    yamlパッケージが未インストールの場合もデフォルト値で動作する。
    """
    defaults = {
        "server": {"host": "0.0.0.0", "port": 8765, "log_level": "INFO"},
        "llm": {
            "provider": "lmstudio",
            "base_url": "http://localhost:1234",
            "api_key": "",
            "model": "",
            "max_concurrent": 3,
            "timeout_seconds": 120,
        },
        "image": {
            # PHASE M-2: 画像処理モード (none / filename_only / caption / lm_studio)
            #   none          : 画像を無視
            #   filename_only : ファイル名のみメタデータに埋め込む (デフォルト・最速)
            #   caption       : mlx-vlm で説明文生成 (Apple Silicon 必須)
            #   lm_studio     : LM Studio Vision API で説明文生成 (OpenAI 互換、Gemma Vision 等)
            "processing_mode": "filename_only",
            "vlm_model": "mlx-community/llava-1.5-7b-4bit",
            # V3.5.0 Stage B: 画像エグレス封鎖。既定 False=ローカル限定。
            # 非ローカル宛へ Base64 画像を送る場合のみ明示的に True にする(opt-in)。
            "allow_remote_vlm": False,
        },
        "rag": {
            "strategy": "hybrid_bm25",
            "default_n_results": 5,
            "vector_weight": 0.7,
            "bm25_weight": 0.3,
            # PHASE A-1: MMR (Maximal Marginal Relevance)
            "mmr_enabled": True,
            "mmr_lambda": 0.7,  # 0.0=多様性最大, 1.0=関連性最大
            "mmr_fetch_k": 20,  # 候補プールの大きさ
            # PHASE A-3: Parent-Child チャンキング
            "parent_child_enabled": True,
            "child_chunk_size": 256,
            "child_chunk_overlap": 32,
            "parent_chunk_size": 1000,
            # PHASE A-4: Hybrid Search 統合方式
            "hybrid_method": "rrf",  # "rrf" | "weighted"
            "rrf_k": 60,  # RRF の平滑化定数 (大きいほど低ランクの寄与が増える)
            # PHASE A-5: Multi-Query RAG
            "multi_query_enabled": True,
            "multi_query_count": 3,  # 元クエリ + N-1 個のバリアントで検索 → RRF 統合
            # PHASE A-6: CRAG (Corrective RAG)
            "crag_enabled": True,
            "crag_max_loops": 1,  # フォローアップ検索の最大回数
            # PHASE A-7: HyDE (Hypothetical Document Embeddings)
            "hyde_enabled": False,  # default OFF (High Quality モードで ON)
            # 低信頼度フォールバック: hits の最大 vector_score (cosine類似度 0〜1) で判定
            # BGE-M3 のノイズフロアは 0.35-0.45 (架空クエリでもこの程度の score が出る)
            # 実存クエリは 0.55-0.75 程度のため 0.50 を境界に設定
            # 高すぎると関連文書取りこぼし、低すぎると無関係文書を返す
            "confidence_threshold": 0.40,
            # Reranker 推論時の最大トークン長 (BAAI/bge-reranker-v2-m3 推奨は 512)
            "reranker_max_length": 512,
        },
        "reranker": {
            "provider": "none",
            "model": "cross-encoder/ms-marco-MiniLM-L-6-v2",
            "base_url": "",
            "api_key": "",
            "top_n": 5,
        },
        "classifier": {
            "provider": "rule_based",
            "api_url": "",
            "api_key": "",
        },
        "vector_store": {
            "provider": "chromadb",
            "path": "",
            "qdrant_url": "http://localhost:6333",
            "qdrant_api_key": "",
        },
        "embedding": {
            "provider": "local",
            "model": "BAAI/bge-m3",
            "base_url": "",
            "api_key": "",
        },
        "chunking": {"chunk_size": 300, "chunk_overlap": 50},
        "logging": {"level": "INFO", "request_id": True},
    }

    config = {k: dict(v) if isinstance(v, dict) else v for k, v in defaults.items()}

    # YAMLファイルの読み込み
    if _YAML_AVAILABLE and Path(yaml_path).exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                file_config = yaml.safe_load(f) or {}
            for section, values in file_config.items():
                if section in config and isinstance(values, dict):
                    config[section].update(values)
                else:
                    config[section] = values
        except Exception as e:
            print(f"[config] cynovela.yaml の読み込みに失敗しました: {e}（デフォルト値で起動）")

    # DD-CYN-0115 R-1: 設定の出どころは「ConfigMap がマウントする cynovela.yaml」
    # ＋「Secret/配置由来の3鍵の直接 env（CYNOVELA_SECRET_KEY / CYNOVELA_DB / CYNOVELA_CHROMA）」に一本化。
    # 旧 env_map（CYNOVELA_LLM_* 等13鍵の汎用上書き）は撤去した。K8s 配置はこれらの env を渡しておらず
    # （phase2/phase4a の両 Deployment とも）、yaml と env が二重に効く事故源だけが残っていたため。
    # 優先順位: DB settings（画面の設定・exec.*）＞ cynovela.yaml ＞ コード既定。

    return config


# モジュールロード時に設定を読み込む
CYNOVELA_CONFIG = load_cynovela_config()


# ─── BLOCK A-1: 起動モード設計 ───


@dataclass
class AppConfig:
    """起動モードと CLI フラグを表現する dataclass。
    --mode と --demo / --mock の組合せで決まる派生プロパティを提供する。
    """

    # full / text / lite / lite-en / minimal
    # lite: paraphrase-multilingual-MiniLM-L12-v2 (470MB, 多言語対応)
    # lite-en: paraphrase-MiniLM-L3-v2 (22MB, 英語特化・低スペック向け)
    mode: str
    demo: bool
    mock: bool

    @property
    def multimodal_enabled(self) -> bool:
        return self.mode == "full"

    @property
    def use_lightweight_embedding(self) -> bool:
        return self.mode in ("lite", "lite-en")

    @property
    def use_tfidf(self) -> bool:
        return self.mode == "minimal"

    @property
    def pytorch_required(self) -> bool:
        return self.mode in ("full", "text", "lite", "lite-en")

    @property
    def embedding_model_name(self) -> str:
        if self.mode == "lite":
            return "paraphrase-multilingual-MiniLM-L12-v2"
        if self.mode == "lite-en":
            return "paraphrase-MiniLM-L3-v2"
        if self.mode == "minimal":
            return "tfidf"
        return "BAAI/bge-m3"


# ─── P1-1: cynovela.yaml の独立ローダー ─────────────────────────────────
# 既存の load_cynovela_config / CYNOVELA_CONFIG はそのまま温存する。
# ここで提供する load_yaml_config / get_yaml_config は P1 系新機能
# (circuit_breaker / ui / rag.citation_enabled 等) からの読み出し専用。

_YAML_CONFIG: dict = {}


def _default_yaml_config() -> dict:
    return {
        "server": {"host": "0.0.0.0", "port": 8765, "log_level": "INFO"},
        "llm": {
            "provider": "lmstudio",
            "base_url": "http://localhost:1234",
            "max_concurrent": 3,
            "timeout_seconds": 120,
        },
        "rag": {
            "strategy": "hybrid_bm25",
            "default_n_results": 5,
            "reranker_enabled": True,
            "reranker_url": None,
            "citation_enabled": True,
        },
        "circuit_breaker": {"enabled": True, "failure_threshold": 3, "recovery_timeout_seconds": 30},
        "ui": {"default_rag_display_mode": "normal"},
        "logging": {"level": "INFO", "request_id": True},
    }


def load_yaml_config(path: str = "cynovela.yaml") -> dict:
    """cynovela.yaml を読み込みキャッシュする。
    ファイル不在 / 読込失敗時はデフォルト値で起動継続する (Lifecycle SL-02 互換)。
    """
    global _YAML_CONFIG
    defaults = _default_yaml_config()
    if not _YAML_AVAILABLE or not os.path.exists(path):
        _YAML_CONFIG = defaults
        return _YAML_CONFIG
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[config] cynovela.yaml 読込失敗: {e}（デフォルト値で起動）")
        _YAML_CONFIG = defaults
        return _YAML_CONFIG
    # デフォルト値をベースに上書きマージ
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in defaults.items()}
    for section, values in loaded.items():
        if isinstance(values, dict) and section in merged and isinstance(merged[section], dict):
            merged[section].update(values)
        else:
            merged[section] = values
    _YAML_CONFIG = merged
    return _YAML_CONFIG


def get_yaml_config() -> dict:
    """ロード済み yaml config を返す。未ロードなら load_yaml_config() を呼ぶ。"""
    if not _YAML_CONFIG:
        load_yaml_config()
    return _YAML_CONFIG


# ─── P4-3: features / sync / execution アクセサ ───

# ─── P4-15: 実行時オーバーライド（メモリ上）────────────────────────────
# DBの settings テーブルから読んだ値をプロセス内にキャッシュして、
# YAMLデフォルトの上に重ねる。サーバー再起動時にDBから再ロードする。
_RUNTIME_FEATURE_OVERRIDES: dict = {}
_RUNTIME_EXEC_OVERRIDES: dict = {}


def set_runtime_feature_override(key: str, enabled: bool) -> None:
    _RUNTIME_FEATURE_OVERRIDES[key] = bool(enabled)


def set_runtime_exec_override(key: str, value) -> None:
    _RUNTIME_EXEC_OVERRIDES[key] = value


def load_runtime_overrides_from_db(get_db_conn) -> None:
    """サーバー起動時に DB settings から `feature.*` / `exec.*` / 画像処理モードを読み込む。"""
    try:
        conn = get_db_conn()
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        finally:
            conn.close()
        _img = CYNOVELA_CONFIG.setdefault("image", {})
        for r in rows:
            k = r["key"] if hasattr(r, "keys") else r[0]
            v = r["value"] if hasattr(r, "keys") else r[1]
            if k.startswith("feature."):
                _RUNTIME_FEATURE_OVERRIDES[k[len("feature.") :]] = str(v).lower() in ("1", "true", "yes", "on")
            elif k.startswith("exec."):
                _RUNTIME_EXEC_OVERRIDES[k[len("exec.") :]] = v
            elif k == "image_processing_mode" and v in ("none", "filename_only", "caption", "lm_studio"):
                _img["processing_mode"] = v
            elif k == "image_vlm_model":
                _img["vlm_model"] = v
            elif k == "image_endpoint":
                _img["endpoint"] = v
    except Exception as e:
        print(f"[config] runtime overrides の読込に失敗: {e}")


def apply_image_setting(key: str, value: str) -> bool:
    """PUT /api/settings から呼ばれる: image_* 設定を即座に CYNOVELA_CONFIG に反映する。
    対象キー: image_processing_mode / image_vlm_model / image_endpoint。
    対象キー以外は False を返す。"""
    _img = CYNOVELA_CONFIG.setdefault("image", {})
    if key == "image_processing_mode":
        if value not in ("none", "filename_only", "caption", "lm_studio"):
            return False
        _img["processing_mode"] = value
        return True
    if key == "image_vlm_model":
        _img["vlm_model"] = value
        return True
    if key == "image_endpoint":
        _img["endpoint"] = value
        return True
    return False


def get_features() -> dict:
    """featuresフラグを返す。存在しないキーはTrueを返す（後方互換）。
    優先順: ランタイムDBオーバーライド > YAML > デフォルト。"""
    defaults = {
        "metadata_engine": True,
        "data_guardrails": True,
        "data_sync": True,
        "audit_log": True,
        "pipeline_visualization": True,
        "session_history": True,
        "feedback": True,
    }
    cfg_features = get_yaml_config().get("features", {}) or {}
    return {**defaults, **cfg_features, **_RUNTIME_FEATURE_OVERRIDES}


def is_feature_enabled(feature: str) -> bool:
    """指定した機能が有効かどうかを返す。"""
    return bool(get_features().get(feature, True))


def get_sync_config() -> dict:
    """sync設定を返す。"""
    defaults = {
        "auto_poll": False,
        "poll_interval_seconds": 3600,
        "auto_publish": True,
        "notify_on_change": True,
    }
    cfg = get_yaml_config().get("sync", {}) or {}
    return {**defaults, **cfg}


def get_execution_config() -> dict:
    """execution設定を返す。優先順: ランタイムDBオーバーライド > YAML > デフォルト。"""
    defaults = {
        "llm_provider": "local",
        "llm_base_url": "http://localhost:1234",
        "openrouter_api_key": "",
        "claude_api_key": "",
        "multimodal": "off",
        "vlm_model": "",
    }
    cfg = get_yaml_config().get("execution", {}) or {}
    return {**defaults, **cfg, **_RUNTIME_EXEC_OVERRIDES}


def detect_multimodal_environment() -> dict:
    """ホスト環境からマルチモーダル推論バックエンドを推定して返す。"""
    import platform as _pl

    out = {"platform": "", "arch": "", "backend": "", "recommended_model": "", "note": ""}
    sysname = _pl.system()
    machine = _pl.machine()
    out["platform"] = sysname
    out["arch"] = machine
    if sysname == "Darwin" and machine in ("arm64", "aarch64"):
        out["backend"] = "mlx-vlm"
        out["recommended_model"] = "mlx-community/Qwen2-VL-7B-Instruct-4bit"
        out["note"] = "Apple Silicon: MLXで高速推論（推奨RAM 12GB以上）"
        return out
    # CUDA detection（torch がインストールされている場合のみ）
    try:
        import torch as _t  # type: ignore

        if hasattr(_t, "cuda") and _t.cuda.is_available():
            out["backend"] = "transformers+CUDA"
            out["recommended_model"] = "Qwen/Qwen2-VL-7B-Instruct"
            out["note"] = "NVIDIA GPU検出（推奨VRAM 8GB以上）"
            return out
    except Exception:
        pass
    out["backend"] = "transformers+CPU"
    out["recommended_model"] = "Qwen/Qwen2-VL-2B-Instruct"
    out["note"] = "CPU実行。処理は遅め（推奨RAM 16GB以上）"
    return out


# ─── Phase 0c: モデルパス自動解決 ───────────────────────────────


def resolve_model_path(model_name: str, configured_path: str = "") -> str:
    """モデルパスを優先順位で解決する。

    優先順位:
      1. configured_path が非空ならそれを返す（cynovela.yaml.models.*.path）
         - ~ / 相対パスは絶対パスに展開する
      2. {app_dir}/store/models/models--<name>/snapshots/*  （★PORTABILITY: TAR 配布同梱 / 別マシン展開時）
      3. {app_dir}/../models--<name with / replaced by -->/snapshots/* （配布パッケージ用 / レガシー）
      4. ~/.cynovela/models/models--<name>/snapshots/*  （sentence_transformers の cache_folder 既定）
      5. ~/.cynovela/hf_cache/models--<name>/snapshots/*  （Cynovela 専用キャッシュ）
      6. ~/.cache/huggingface/hub/models--<name>/snapshots/*  （HF デフォルトキャッシュ）
      7. 上記すべて見つからない場合は model_name をそのまま返す
         （sentence_transformers が HF からダウンロードする）

    返り値はそのまま `SentenceTransformer(...)` / `CrossEncoder(...)` の第一引数に渡せる。
    """
    import glob as _glob

    if configured_path:
        return os.path.abspath(os.path.expanduser(configured_path))

    folder = "models--" + (model_name or "").replace("/", "--")
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(app_dir, "store", "models", folder),  # PORTABILITY FIX: TAR 配布同梱
        os.path.join(os.path.dirname(app_dir), folder),
        os.path.expanduser(os.path.join("~/.cynovela/models", folder)),
        os.path.expanduser(os.path.join("~/.cynovela/hf_cache", folder)),
        os.path.expanduser(os.path.join("~/.cache/huggingface/hub", folder)),
    ]
    for base in candidates:
        snapshots = sorted(_glob.glob(os.path.join(base, "snapshots", "*")), reverse=True)
        # config.json を持つ完全なスナップショットを優先（不完全な重複コピー回避）
        for snap in snapshots:
            if os.path.isdir(snap) and os.path.exists(os.path.join(snap, "config.json")):
                return snap
        for snap in snapshots:
            if os.path.isdir(snap):
                return snap

    return model_name


def get_configured_model(kind: str) -> tuple[str, str]:
    """cynovela.yaml.models.<kind> から (name, path) を返す。

    kind: "embedding" | "reranker"
    name が未設定の場合は providers 側のデフォルトに任せるため空文字を返す。
    """
    cfg = (CYNOVELA_CONFIG.get("models") or {}).get(kind) or {}
    name = (cfg.get("name") or "").strip()
    path = (cfg.get("path") or "").strip()
    return name, path
