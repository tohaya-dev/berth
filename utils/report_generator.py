"""
AI レポート生成 (PHASE 11)

出力形式: Markdown + HTML (変換は標準ライブラリのみ)
日本語・英語両対応
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Dict, List, Optional


PROMPTS = {
    "poc_ja": """以下の情報をもとにPoC結果レポートを作成してください。

ワークスペース: {workspace_name}
期間: {period}
チャット回数: {chat_count}
参照文書数: {source_count}
PII検出数: {pii_count}
チャット概要: {history_summary}

# PoC結果レポート
## エグゼクティブサマリー
## 検証した内容
## 主な発見事項
## ローカルLLM活用によるコスト削減効果（試算）
## 推奨事項
## 次のステップ""",
    "poc_en": """Create a PoC Results Report based on the following data.

Workspace: {workspace_name}
Period: {period}
Chat sessions: {chat_count}
Documents referenced: {source_count}
PII detected: {pii_count}
Chat summary: {history_summary}

# PoC Results Report
## Executive Summary
## What Was Tested
## Key Findings
## Cost Savings from Local LLM (Estimate)
## Recommendations
## Next Steps""",
    "monthly_ja": """以下の運用データをもとに月次レポートを作成してください。
期間: {period} / データ: {stats}

# 月次運用レポート {period}
## サマリー
## クエリ統計
## ガバナンス状況
## コスト節約実績
## 来月の推奨アクション""",
    "monthly_en": """Create a monthly operations report from the data below.
Period: {period} / Data: {stats}

# Monthly Operations Report — {period}
## Summary
## Query Statistics
## Governance Status
## Cost Savings
## Recommended Actions for Next Month""",
}


def generate_report(
    report_type: str,
    llm_client,
    chat_history: List[Dict],
    workspace_name: str,
    stats: Optional[Dict] = None,
    lang: str = "ja",
) -> Dict:
    """LLM にプロンプトを送って Markdown レポートを生成。
    LLM が応答しない場合はエラー文言を含む Markdown を返す。"""
    prompt_key = f"{report_type}_{lang}"
    if prompt_key not in PROMPTS:
        prompt_key = f"{report_type}_ja"
    if prompt_key not in PROMPTS:
        # 完全に未知の report_type → 最初のプロンプトを使う
        prompt_key = next(iter(PROMPTS.keys()))

    history_summary = " / ".join(
        m.get("content", "")[:100] for m in (chat_history or [])[-10:] if m.get("role") == "user"
    )

    s = stats or {}
    period_fmt = "%Y年%m月" if lang == "ja" else "%Y-%m"
    prompt = PROMPTS[prompt_key].format(
        workspace_name=workspace_name,
        period=datetime.now().strftime(period_fmt),
        chat_count=s.get("chat_count", 0),
        source_count=s.get("source_count", 0),
        pii_count=s.get("pii_count", 0),
        history_summary=(history_summary or "")[:500],
        stats=json.dumps(s, ensure_ascii=False),
    )

    try:
        response = llm_client.chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.3,
        )
        markdown_text = (response or {}).get("content", "")
        if not markdown_text:
            raise RuntimeError("LLM returned empty content")
    except Exception as e:
        markdown_text = f"# レポート生成エラー\n{e}" if lang == "ja" else f"# Report Generation Error\n{e}"

    return {
        "markdown": markdown_text,
        "html": _md_to_html(markdown_text),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report_type": report_type,
        "lang": lang,
    }


def _md_to_html(md: str) -> str:
    """超軽量 Markdown→HTML 変換 (依存ライブラリなし)"""
    html = md or ""
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
    html = html.replace("\n\n", "</p><p>")
    return f'<article class="cynovela-report"><p>{html}</p></article>'
