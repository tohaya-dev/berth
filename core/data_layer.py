"""Phase A: データ層差し替え口（一枚）。

関係データ（SQLite / Postgres）と ベクター（Chroma / pgvector）のバックエンドを
`cynovela.yaml` の `database.backend` で一括選択する単一エントリ。

- `backend: sqlite`（既定）= 関係:SQLite ＋ ベクター:Chroma（別々の保管庫・別接続）
- `backend: postgres`（Phase B）= 関係:Postgres ＋ ベクター:pgvector（同一接続に統合）

既定実装は現行 SQLite + Chroma をそのまま裏に置き、**挙動を一切変えない**。
hot path（`db.get_db` / `rag._get_vs`）は本モジュールと同じ設定キーを参照する。
ORM の自動マッピングには頼らず、実装は物理的に分離する（db.py / providers/vector_store.py）。
"""

from __future__ import annotations

_PG_BACKENDS = ("postgres", "postgresql", "pgvector")


def _backend_name() -> str:
    """cynovela.yaml `database.backend` を読む。未設定/失敗時は 'sqlite'。"""
    try:
        from core.config import CYNOVELA_CONFIG as _DTC

        return ((_DTC.get("database") or {}).get("backend") or "sqlite").lower()
    except Exception:
        return "sqlite"


class DataLayer:
    """関係 + ベクターの統合差し替え口。

    relational_backend() … 関係データ接続を生成するバックエンド（db.py が実体）
    vector_store()       … ベクター操作の Provider（providers/vector_store.py が実体）
    """

    def __init__(self, backend: str):
        self.backend = backend

    @property
    def is_unified(self) -> bool:
        """関係とベクターが同一接続に載るバックエンドか（Postgres+pgvector）。"""
        return self.backend in _PG_BACKENDS

    def relational_backend(self):
        from db import get_relational_backend

        return get_relational_backend()

    def relational_connection(self):
        return self.relational_backend().connect()

    def vector_store(self):
        from providers.vector_store import get_vector_store_provider

        try:
            from core.config import CYNOVELA_CONFIG as _cfg
        except Exception:
            _cfg = {}
        return get_vector_store_provider(_cfg)

    def describe(self) -> dict:
        return {
            "backend": self.backend,
            "relational": "postgres" if self.is_unified else "sqlite",
            "vector": "pgvector" if self.is_unified else "chromadb",
            "unified_connection": self.is_unified,
        }


_DATA_LAYER: "DataLayer | None" = None


def get_data_layer(config: dict | None = None) -> DataLayer:
    """現在有効な DataLayer（単一 swap-point）を返す。"""
    global _DATA_LAYER
    if _DATA_LAYER is None:
        _DATA_LAYER = DataLayer(_backend_name())
    return _DATA_LAYER


def reset_data_layer() -> None:
    """テスト / バックエンド再選択用にキャッシュを破棄する。"""
    global _DATA_LAYER
    _DATA_LAYER = None
