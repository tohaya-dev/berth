"""core.config — 設定ロードの単一エントリポイント。

Stage R1-1A: 既存 config.py からシンボルを re-export する薄いラッパー。
Stage R3/R4 で実体を core/ に移植し、config.py を deprecate する。
"""

from __future__ import annotations

from config import (
    CYNOVELA_CONFIG,
    apply_image_setting,
    decrypt,
    detect_multimodal_environment,
    encrypt,
    get_execution_config,
    get_features,
    get_sync_config,
    get_yaml_config,
    is_feature_enabled,
    load_cynovela_config,
    load_runtime_overrides_from_db,
    load_yaml_config,
    set_runtime_exec_override,
    set_runtime_feature_override,
)

__all__ = [
    "CYNOVELA_CONFIG",
    "apply_image_setting",
    "decrypt",
    "detect_multimodal_environment",
    "encrypt",
    "get_execution_config",
    "get_features",
    "get_sync_config",
    "get_yaml_config",
    "is_feature_enabled",
    "load_cynovela_config",
    "load_runtime_overrides_from_db",
    "load_yaml_config",
    "set_runtime_exec_override",
    "set_runtime_feature_override",
]
