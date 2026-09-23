"""core.schemas — routers/ の入力 BaseModel スキーマ集約。

Stage R3-2 で新設。各 router の主要 EP に対する Pydantic スキーマを
モジュール別に格納する。

実装方針:
- BaseSchema (extra=forbid, str_strip_whitespace) を基底とする
- ただし routers の既存 body 構造に追加フィールドが含まれる可能性を考慮し、
  各 EP の用途に応じて extra="allow" 派生も使う
- 主要フィールドのみ厳密型付け、副次フィールドは Optional/Any
"""

from core.schemas.admin import (
    CreateUserBody,
    PasswordResetBody,
    UpdateUserBody,
)
from core.schemas.chat import (
    ChatCompareBody,
    ChatPresetBody,
    ChatRequestBody,
    ChatSummarizeBody,
)
from core.schemas.collection import (
    CreateCollectionBody,
    UpdateCollectionBody,
)
from core.schemas.settings import (
    ACLPolicyBody,
    ClassifierConfigBody,
    EmbeddingConfigBody,
    FeatureToggleBody,
    LLMConfigBody,
    PIIDetectionModeBody,
    RerankerConfigBody,
    VectorStoreConfigBody,
)
from core.schemas.workspace import (
    CreateWorkspaceBody,
    UpdateWorkspaceBody,
    WorkspacePolicyBody,
    WorkspaceSyncConfigBody,
)

__all__ = [
    "ACLPolicyBody",
    "ChatCompareBody",
    "ChatPresetBody",
    "ChatRequestBody",
    "ChatSummarizeBody",
    "ClassifierConfigBody",
    "CreateCollectionBody",
    "CreateUserBody",
    "CreateWorkspaceBody",
    "EmbeddingConfigBody",
    "FeatureToggleBody",
    "LLMConfigBody",
    "PIIDetectionModeBody",
    "PasswordResetBody",
    "RerankerConfigBody",
    "UpdateCollectionBody",
    "UpdateUserBody",
    "UpdateWorkspaceBody",
    "VectorStoreConfigBody",
    "WorkspacePolicyBody",
    "WorkspaceSyncConfigBody",
]
