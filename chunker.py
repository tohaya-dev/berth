"""Cynovela — Contextual Chunking (Phase 2)

Contextual Retrieval 手法をルールベースで再現するチャンキングモジュール。
LLM 呼び出しは行わず、ファイルメタデータ（タイトル・カテゴリ・感度・部門・順序）
をチャンク冒頭にコンテキスト文として付加する。
"""

from __future__ import annotations


def is_contextual_enabled(default: bool = False) -> bool:
    """cynovela.yaml.chunking.contextual で ON/OFF。
    DB settings テーブルの 'chunking.contextual' があれば最優先で適用する。
    """
    # DB ランタイムオーバーライド優先
    try:
        from db import get_db as _gdb

        c = _gdb()
        try:
            r = c.execute("SELECT value FROM settings WHERE key='chunking.contextual'").fetchone()
        finally:
            c.close()
        if r is not None:
            return str(r["value"]).lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    try:
        from core.config import get_yaml_config as _gyc

        v = (_gyc().get("chunking") or {}).get("contextual", default)
        return bool(v)
    except Exception:
        return default


def build_context_prefix(
    *,
    file_name: str = "",
    doc_type: str = "",
    sensitivity: str = "",
    department: str = "",
    chunk_index: int = 0,
    total_chunks: int = 1,
    auto_tags: list[str] | None = None,
) -> str:
    """チャンクの冒頭に付加するコンテキスト文を生成する（短く宣言的）。"""
    parts: list[str] = []
    if file_name:
        parts.append(f"文書: {file_name}")
    if doc_type and doc_type != "general":
        parts.append(f"種別: {doc_type}")
    if sensitivity and sensitivity != "public":
        parts.append(f"感度: {sensitivity}")
    if department:
        parts.append(f"部門: {department}")
    if total_chunks > 1:
        parts.append(f"位置: {chunk_index + 1}/{total_chunks}番目のセクション")
    if auto_tags:
        tag_str = ", ".join(auto_tags[:3])
        if tag_str:
            parts.append(f"タグ: {tag_str}")
    if not parts:
        return ""
    return "[コンテキスト] " + " | ".join(parts) + "\n\n"


def apply_context(chunk_text: str, context_prefix: str) -> str:
    """チャンク本文の前にコンテキスト文を付加する。"""
    if not context_prefix:
        return chunk_text or ""
    return f"{context_prefix}{chunk_text or ''}"
