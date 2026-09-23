"""静的ページ配信ルーター。

/ (フロントエンド index.html) と /chat-popup (廃止: 410 Gone) を提供。
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from core.errors import api_error


router = APIRouter(tags=["pages"])


@router.get("/", response_model=None)
async def serve_index():
    # settings-reflect-cachebust-fix-20260628 (F1): index.html は no-store で毎回取得させ、
    # 旧 index（古い /frontend/js 参照）の使い回しを防ぐ。
    return FileResponse(
        "frontend/index.html",
        headers={"Cache-Control": "no-store"},
    )


# R2: /chat-popup ルートはサイドパネル削除に伴い廃止。
# 旧 URL を踏んだクライアントには 410 Gone を返してフルスクリーン Chat への切替を促す。
@router.get("/chat-popup", response_model=None)
async def chat_popup_page_removed():
    raise api_error(
        "GONE",
        "Chat popup mode has been removed. Use the full-screen Chat page instead.",
        status=410,
    )
