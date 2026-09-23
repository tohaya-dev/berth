"""Cynovela — Adaptive RAG (Phase 1)

質問の複雑度を判定し、Basic / Agentic を自動切替する。
- Basic: 通常の1回検索
- Agentic: 最大 N 回の検索＋自己評価ループ（モック時は1回で打ち切り）

判定はルールベース（LLM不使用）。閾値は cynovela.yaml.rag.adaptive_threshold で調整可能。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 複雑度スコアの加点キーワード（日英）
_COMPLEXITY_KEYWORDS = [
    # 比較・分析
    "比較",
    "違い",
    "対比",
    "分析",
    "評価",
    "compare",
    "difference",
    "analyze",
    "analysis",
    "evaluate",
    # 理由・原因
    "なぜ",
    "理由",
    "原因",
    "どのように",
    "どうやって",
    "why",
    "how",
    "reason",
    "cause",
    # 列挙・推奨
    "それぞれ",
    "リストアップ",
    "全て",
    "推奨",
    "提案",
    "list",
    "enumerate",
    "recommend",
    "suggest",
    # 因果・条件
    "影響",
    "結果",
    "もし",
    "条件",
    "impact",
    "result",
    "if",
]

# 複雑な質問とみなす接続詞（複数の問いを内包する）
_MULTI_QUESTION_MARKERS = ["かつ", "および", "and", "また", "ならびに", "それから"]


def _config_threshold(default: float = 2.0) -> float:
    try:
        from core.config import get_yaml_config as _gyc

        v = (_gyc().get("rag") or {}).get("adaptive_threshold", default)
        return float(v)
    except Exception:
        return default


def _config_max_loops(default: int = 3) -> int:
    try:
        from core.config import get_yaml_config as _gyc

        v = (_gyc().get("rag") or {}).get("agentic_max_loops", default)
        return max(1, min(int(v), 5))
    except Exception:
        return default


def _config_enabled(default: bool = True) -> bool:
    try:
        from core.config import get_yaml_config as _gyc

        v = (_gyc().get("rag") or {}).get("adaptive_enabled", default)
        return bool(v)
    except Exception:
        return default


@dataclass
class ComplexityResult:
    score: float
    mode: str  # "basic" or "agentic"
    threshold: float
    reasons: list[str]


def score_query_complexity(query: str) -> ComplexityResult:
    """質問文を分析し、Basic / Agentic を判定する。"""
    q = (query or "").strip()
    score = 0.0
    reasons: list[str] = []

    # 1) 文長: 50文字超で +1.0、100文字超で更に +0.5
    if len(q) > 50:
        score += 1.0
        reasons.append(f"長い質問 ({len(q)}文字)")
    if len(q) > 100:
        score += 0.5
        reasons.append("詳細な質問")

    # 2) 複雑度キーワード: マッチ毎に +0.5
    qlower = q.lower()
    matched_kw: list[str] = []
    for kw in _COMPLEXITY_KEYWORDS:
        if kw.lower() in qlower:
            matched_kw.append(kw)
    if matched_kw:
        score += 0.5 * len(set(matched_kw[:6]))  # 上限6種で頭打ち
        reasons.append(f"複雑キーワード: {', '.join(matched_kw[:3])}")

    # 3) 複数質問マーカー: マッチ毎に +0.5
    multi_hits = [m for m in _MULTI_QUESTION_MARKERS if m.lower() in qlower]
    if multi_hits:
        score += 0.5 * len(multi_hits)
        reasons.append(f"複合質問: {','.join(multi_hits[:2])}")

    # 4) 疑問符の数
    q_marks = q.count("?") + q.count("？")
    if q_marks >= 2:
        score += 0.5
        reasons.append("疑問符複数")

    threshold = _config_threshold()
    mode = "agentic" if (score >= threshold and _config_enabled()) else "basic"
    return ComplexityResult(score=round(score, 2), mode=mode, threshold=threshold, reasons=reasons)


def derive_followup_query(original: str, current_answer: str) -> str:
    """Agentic ループの追加質問を生成する（ルールベース）。
    現状の回答が短すぎる/曖昧なら、元質問の中から追加で深掘りする観点を抽出する。
    """
    base = (original or "").strip()
    # シンプル: 「詳細」「具体例」を補強した派生クエリ
    return f"{base} 具体例や根拠を含めて補足"


@dataclass
class AgenticLoopRecord:
    iteration: int
    query: str
    n_hits: int
    self_eval: str  # "sufficient" | "insufficient"
    note: str


def evaluate_answer_quality(answer: str, n_hits: int) -> tuple[str, str]:
    """自己評価（ルールベース）: 「この情報で十分か？」を返す。
    Returns (verdict, note)
    verdict: 'sufficient' or 'insufficient'
    """
    a = (answer or "").strip()
    if not a:
        return "insufficient", "回答が空"
    if len(a) < 60 and n_hits < 2:
        return "insufficient", f"短い回答 ({len(a)}文字) かつヒット少 ({n_hits})"
    # 「分かりません」「情報がない」などの否定表現
    neg = ["分かりません", "情報がありません", "I don't know", "no information", "見つかりません", "提供されていません"]
    # ga-close-v3 PartE E-1: LLM 不在・生成タイムアウト時にシステム側が返す定型文も
    #   「足りない」に数える。従来はこの2文が一覧に無いため、回答が1文字も生成できて
    #   いないのに必ず「十分」と判定され、追加検索へ一度も入らなかった。
    #   判定の仕組み (長さ・ヒット数・否定表現の3段) は一切変えず、一覧に足すだけ。
    #   文字列は routers/chat.py の定型文と同一 (変更時は両方を追従させること)。
    #   DD-CYN-0143 C-7 修正後は失敗が HTTPException / SSE error イベント化され新規発生しない
    #   (旧履歴に残る定型文の後方互換のため残置)。
    neg += ["LLMへの接続に失敗しました", "回答の生成に時間がかかり、タイムアウトしました"]
    if any(n in a for n in neg):
        return "insufficient", "否定的な回答"
    return "sufficient", f"回答十分 ({len(a)}文字, {n_hits}件)"
