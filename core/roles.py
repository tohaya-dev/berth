"""core.roles — ユーザーロールの単一定義。

core/constants.py の VALID_ROLES と ROLE_DEMO_WS_NAME を再エクスポートする。

Stage R2-6 完了時点で DB migration により legacy ロールは存在しないため、
ROLE_NORMALIZE は空 dict (アイデンティティ写像)。normalize_role() 関数は
将来 legacy 互換が必要になった場合の単一の集約点として残す。
"""

from __future__ import annotations

from core.constants import ROLE_DEMO_WS_NAME, VALID_ROLES

# Stage R2 migration 完了でアイデンティティ写像 (空 dict)。
# DB は viewer/admin の 2 値のみ保持する。
ROLE_NORMALIZE: dict[str, str] = {}


def normalize_role(role: str) -> str:
    """ロールを新表現に正規化。Stage R2 完了後はアイデンティティ。"""
    return ROLE_NORMALIZE.get(role, role)


__all__ = ["ROLE_DEMO_WS_NAME", "ROLE_NORMALIZE", "VALID_ROLES", "normalize_role"]
