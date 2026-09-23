"""core.acl — ACL 関連関数の単一エントリポイント。

rag.py:1456 の _normalize_role_to_acl を core/ に re-export する。

Phase 3 Recon Agent G §1.1 で確認: routers/chat.py が rag.py から直接 import している
(chat.py:545/897/1518/1766 の 4 箇所)。Stage R1-2B でこれらの import を core.acl 経由に書換える。

Stage R1-1E: 既存 rag.py からの re-export ラッパー。
Stage R5/R6 で実体を core/ に移植する。
"""

from __future__ import annotations

from rag import _normalize_role_to_acl

__all__ = ["_normalize_role_to_acl"]
