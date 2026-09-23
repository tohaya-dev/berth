"""Structured API error helper.

新規エンドポイントは {"error": "ERROR_CODE", "message": "English"} 形式を採用する。
既存の HTTPException(status, "...") は段階的に移行する (互換維持)。
"""

from __future__ import annotations

from fastapi import HTTPException


def api_error(code: str, message: str, status: int = 400) -> HTTPException:
    """構造化されたエラー詳細を持つ HTTPException を返す。

    Usage:
        raise api_error("COLLECTION_NOT_FOUND", "Collection not found", 404)
    """
    return HTTPException(status_code=status, detail={"error": code, "message": message})
