"""chat.py 専用ヘルパー関数 (A-5 cleanup で漏れた分を集約).

server.py から parse_policy_ids と _get_effective_system_prompt を切り出し。
chat.py + workspaces.py の両方から static import される。
"""

from __future__ import annotations

from typing import Optional
import json

from rag import DEFAULT_SYSTEM_PROMPT, apply_role_prefix
from db import get_db


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
