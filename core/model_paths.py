"""core.model_paths — モデルパス解決の単一エントリポイント。

config.resolve_model_path / get_configured_model を core/ に re-export する。

Phase 3 Recon Agent B §3 で確認: 本番呼び出しは providers/embedding.py:63 と
providers/reranker.py:91 の 2 箇所のみ（utils/embedding.py / utils/reranker.py は
Stage R0 で削除済み）。

Stage R1-1C: 既存 config.py からの re-export ラッパー。
Stage R4 で _wire_providers_for_mode を経由した一本化を行う。
"""

from __future__ import annotations

from config import get_configured_model, resolve_model_path

__all__ = ["get_configured_model", "resolve_model_path"]
