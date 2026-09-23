"""utils.metadata.classification — ドキュメント分類 (実体集約版)。

Stage R6-fix (Phase 3-fix): 旧 utils/classification_engine.py の 14 カテゴリ
(LightweightClassifier / LLMClassifier / HybridClassifier) と
旧 metadata_engine.classify_document_type の 5 種 API を本ファイルに統合移植。

14 カテゴリ: Notion「Cynovela Smart Ingestion 設計仕様（2026-05-07）」
5 種 API: 旧 metadata_engine.py の Public API (classify_document_type)
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any

# ============================================================
# 14 カテゴリ Smart Ingestion
# ============================================================

CATEGORIES = {
    "governance_policy": "ガバナンス・ポリシー文書",
    "incident_report": "インシデントレポート",
    "technical_guide": "技術ガイド・マニュアル",
    "case_study": "導入事例",
    "meeting_minutes": "会議議事録",
    "audit_report": "監査・評価報告書",
    "poc_report": "POC評価報告書",
    "faq": "FAQ・よくある質問",
    "whitepaper": "ホワイトペーパー",
    "checklist": "チェックリスト",
    "proposal_rfp": "提案書・RFP",
    "newsletter": "ニュースレター・技術情報",
    "reference": "リファレンス・用語集",
    "other": "その他",
}


class ClassificationEngine(ABC):
    @abstractmethod
    def classify(self, filename: str, file_path: str, content_preview: str) -> dict:
        pass


class LightweightClassifier(ClassificationEngine):
    """ファイル名・先頭500文字のキーワードマッチで分類。"""

    FILENAME_RULES = [
        (["incident", "障害", "インシデント"], "incident_report", 0.85),
        (["minutes", "議事録", "meeting"], "meeting_minutes", 0.85),
        (["audit", "監査", "評価報告"], "audit_report", 0.85),
        (["poc", "評価", "検証"], "poc_report", 0.85),
        (["faq", "よくある", "質問"], "faq", 0.85),
        (["whitepaper", "wp_", "白書"], "whitepaper", 0.85),
        (["checklist", "チェック"], "checklist", 0.85),
        (["rfp", "proposal", "提案"], "proposal_rfp", 0.85),
        (["newsletter", "技術情報"], "newsletter", 0.85),
        (["glossary", "用語"], "reference", 0.85),
    ]

    CONTENT_RULES = [
        (["ポリシー", "規程", "方針"], "governance_policy", 0.65),
        (["ガイドライン", "手順書"], "technical_guide", 0.65),
        (["case study", "導入事例", "事例"], "case_study", 0.65),
    ]

    def classify(self, filename: str, file_path: str, content_preview: str) -> dict:
        name_lower = (filename or "").lower()
        content_lower = (content_preview or "")[:500].lower()

        for keywords, category, confidence in self.FILENAME_RULES:
            if any(kw.lower() in name_lower for kw in keywords):
                return {"category": category, "confidence": confidence, "tags": [], "classified_by": "lightweight"}

        for keywords, category, confidence in self.CONTENT_RULES:
            if any(kw.lower() in content_lower for kw in keywords):
                return {"category": category, "confidence": confidence, "tags": [], "classified_by": "lightweight"}

        return {"category": "other", "confidence": 0.30, "tags": [], "classified_by": "lightweight"}


class LLMClassifier(ClassificationEngine):
    """ローカルLLM（Ollama）を使ったゼロショット文書分類。"""

    CATEGORIES_PROMPT = """あなたは文書分類器です。以下の14カテゴリのうち最も適切な1つを選んでください。
カテゴリ以外の出力は禁止。必ずJSONで返してください。

カテゴリ:
technical_guide, meeting_minutes, incident_report, proposal_rfp,
audit_report, poc_report, whitepaper, faq, checklist, reference,
governance_policy, newsletter, case_study, other

出力形式（必ずこの形式のみ）:
{"category": "カテゴリ名", "confidence": 0.0〜1.0, "reason": "理由を20文字以内で"}

文書の先頭1000文字:
{text}
"""

    VALID_CATEGORIES = set(CATEGORIES.keys())

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._available: bool | None = None

    def _check_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import requests

            r = requests.get(f"{self.base_url}/api/tags", timeout=2)
            self._available = r.status_code == 200
        except Exception:
            self._available = False
        return self._available

    def classify(self, filename: str, file_path: str, content_preview: str) -> dict:
        if not self._check_available():
            return {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "llm_unavailable"}
        try:
            import json

            import requests

            prompt = self.CATEGORIES_PROMPT.replace("{text}", (content_preview or "")[:1000])
            payload = {"model": self.model, "prompt": prompt, "stream": False, "options": {"temperature": 0}}
            r = requests.post(f"{self.base_url}/api/generate", json=payload, timeout=30)
            if r.status_code != 200:
                return {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "llm_error"}
            response_text = r.json().get("response", "")
            m = re.search(r"\{[^}]+\}", response_text)
            if not m:
                return {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "llm_parse_error"}
            result = json.loads(m.group())
            category = result.get("category", "other")
            confidence = float(result.get("confidence", 0.5))
            if category not in self.VALID_CATEGORIES:
                category = "other"
                confidence = 0.3
            return {"category": category, "confidence": confidence, "tags": [], "classified_by": "llm"}
        except Exception:
            return {"category": "other", "confidence": 0.0, "tags": [], "classified_by": "llm_error"}


class HybridClassifier(ClassificationEngine):
    """Lightweight を優先し、confidence が低い場合のみ LLM にフォールバック。"""

    LLM_FALLBACK_THRESHOLD = 0.65

    def __init__(self, llm_base_url: str = "http://localhost:11434", llm_model: str = "llama3"):
        self._lw = LightweightClassifier()
        self._llm = LLMClassifier(base_url=llm_base_url, model=llm_model)

    def classify(self, filename: str, file_path: str, content_preview: str) -> dict:
        lw = self._lw.classify(filename, file_path, content_preview)
        if lw["confidence"] >= self.LLM_FALLBACK_THRESHOLD:
            return lw
        llm = self._llm.classify(filename, file_path, content_preview)
        if llm.get("classified_by") == "llm" and llm["confidence"] >= self.LLM_FALLBACK_THRESHOLD:
            return llm
        return lw


def get_classifier(engine: str = "lightweight") -> ClassificationEngine:
    """ファクトリ関数。"""
    if engine == "lightweight":
        return LightweightClassifier()
    elif engine == "llm":
        return LLMClassifier()
    elif engine == "hybrid":
        return HybridClassifier()
    else:
        raise ValueError(f"Unknown engine: {engine}. Use 'lightweight', 'llm', or 'hybrid'.")


# ============================================================
# 5 種 API (旧 metadata_engine.classify_document_type)
# ============================================================


DOCUMENT_TYPE_RULES: list[dict[str, Any]] = [
    {
        "type": "contract",
        "patterns": [r"(contract|agreement|terms\s+and\s+conditions|甲|乙|契約|覚書)"],
        "label_en": "Contract",
        "label_ja": "契約書",
    },
    {
        "type": "technical_spec",
        "patterns": [r"(specification|仕様書|設計書|architecture|API\s+reference)"],
        "label_en": "Technical Spec",
        "label_ja": "技術仕様書",
    },
    {
        "type": "email",
        "patterns": [r"(^From:|^To:|^Subject:|^CC:|差出人|宛先|件名)"],
        "label_en": "Email",
        "label_ja": "メール",
    },
    {
        "type": "report",
        "patterns": [r"(report|レポート|報告書|summary|エグゼクティブサマリー)"],
        "label_en": "Report",
        "label_ja": "レポート",
    },
    {
        "type": "manual",
        "patterns": [r"(manual|マニュアル|手順書|procedure|操作手順|ガイド)"],
        "label_en": "Manual",
        "label_ja": "マニュアル",
    },
]


def classify_document_type(text: str) -> dict[str, Any]:
    """ドキュメント先頭 5000 文字から種別を推定."""
    sample = (text or "")[:5000]
    for rule in DOCUMENT_TYPE_RULES:
        for pat in rule["patterns"]:
            if re.search(pat, sample, re.IGNORECASE | re.MULTILINE):
                return {"type": rule["type"], "label_en": rule["label_en"], "label_ja": rule["label_ja"]}
    return {"type": "general", "label_en": "General", "label_ja": "一般"}


__all__ = [
    "CATEGORIES",
    "ClassificationEngine",
    "DOCUMENT_TYPE_RULES",
    "HybridClassifier",
    "LLMClassifier",
    "LightweightClassifier",
    "classify_document_type",
    "get_classifier",
]
