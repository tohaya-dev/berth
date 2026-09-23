"""core.schemas.settings — settings 系 EP の入力スキーマ。

各 EP の body 構造は routers/settings.py の `body.get(...)` 行から抽出。
extra="allow" で既存 EP の副次フィールド (description / __meta__ など) を許容。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_OPEN = ConfigDict(extra="allow", str_strip_whitespace=True)


class RerankerConfigBody(BaseModel):
    """POST /api/settings/reranker"""

    model_config = _OPEN
    provider: str = "none"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    top_n: int = 5


class ClassifierConfigBody(BaseModel):
    """POST /api/settings/classifier"""

    model_config = _OPEN
    provider: str = "rule_based"
    api_url: str = ""
    api_key: str = ""


class VectorStoreConfigBody(BaseModel):
    """POST /api/settings/vector-store"""

    model_config = _OPEN
    provider: str = "chromadb"
    path: str = ""
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""


class EmbeddingConfigBody(BaseModel):
    """POST /api/settings/embedding"""

    model_config = _OPEN
    provider: str = "local"
    model: str = ""
    base_url: str = ""
    api_key: str = ""


class LLMConfigBody(BaseModel):
    """POST /api/settings/llm"""

    model_config = _OPEN
    provider: str = "lmstudio"
    base_url: str = "http://localhost:1234"
    model: str = ""
    api_key: str = ""


class ACLPolicyBody(BaseModel):
    """POST /api/settings/acl-policy"""

    model_config = _OPEN
    acl_info: dict[str, Any] = Field(default_factory=dict)
    role_mapping: dict[str, Any] = Field(default_factory=dict)


class FeatureToggleBody(BaseModel):
    """POST /api/settings/feature"""

    model_config = _OPEN
    enabled: bool = False


class PIIDetectionModeBody(BaseModel):
    """POST /api/settings/pii-detection-mode"""

    model_config = _OPEN
    mode: str = "standard"
