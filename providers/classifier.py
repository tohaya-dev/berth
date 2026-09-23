"""Cynovela — ClassifierProvider 抽象層。

PII 検出ロジック（現状は guardrail.py / rag.py のregexベース）を
Providerでラップし、外部分類APIへ差し替え可能にする。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
import httpx


@dataclass
class ClassificationResult:
    pii_detected: bool
    pii_types: list[str] = field(default_factory=list)
    should_exclude: bool = False
    masked_text: str = ""


class ClassifierProvider:
    async def classify(self, text: str) -> ClassificationResult:
        raise NotImplementedError


# ─── 既存ロジックを移植したルールベース実装 ───

_PERSON_PAT = re.compile(r"")  # ルールベースでは固有名詞は扱わない
_EMAIL_PAT = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
_PHONE_PAT = re.compile(r"0\d{1,4}-?\d{1,4}-?\d{4}")
_MYNUMBER_PAT = re.compile(r"\b\d{12}\b")


class RuleBasedClassifier(ClassifierProvider):
    """既存の guardrail.mask_pii / rag.py のregex検出をまとめたデフォルト実装。"""

    async def classify(self, text: str) -> ClassificationResult:
        text = text or ""
        types: list[str] = []
        if _EMAIL_PAT.search(text):
            types.append("EMAIL")
        if _PHONE_PAT.search(text):
            types.append("PHONE")
        if _MYNUMBER_PAT.search(text):
            types.append("MYNUMBER")

        masked = text
        if "EMAIL" in types:
            masked = re.sub(r"([\w])[.\w-]*@[.\w-]*(\.\w+)", r"\1***@***\2", masked)
        if "PHONE" in types:
            masked = re.sub(r"0[789]0-?\d{4}-?\d{4}", "***-****-****", masked)
            masked = re.sub(r"0\d{1,4}-?\d{1,4}-?\d{4}", "***-****-****", masked)
        if "MYNUMBER" in types:
            masked = re.sub(r"\b\d{12}\b", "************", masked)

        return ClassificationResult(
            pii_detected=bool(types),
            pii_types=types,
            should_exclude=False,  # exclude判定はWS側のpolicyで上書き
            masked_text=masked,
        )


class APIClassifier(ClassifierProvider):
    """外部分類APIへ委譲する Provider。"""

    def __init__(self, api_url: str = "", api_key: str = ""):
        self.api_url = (api_url or "").rstrip("/")
        self.api_key = api_key or os.environ.get("CYNOVELA_CLASSIFIER_API_KEY", "") or ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def classify(self, text: str) -> ClassificationResult:
        if not self.api_url:
            # APIが未設定ならルールベースにフォールバック
            return await RuleBasedClassifier().classify(text)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    self.api_url,
                    headers=self._headers(),
                    json={"text": text or ""},
                )
                r.raise_for_status()
                data = r.json()
                return ClassificationResult(
                    pii_detected=bool(data.get("pii_detected")),
                    pii_types=list(data.get("pii_types") or []),
                    should_exclude=bool(data.get("should_exclude")),
                    masked_text=data.get("masked_text") or text or "",
                )
        except Exception:
            return await RuleBasedClassifier().classify(text)


def get_classifier_provider(config: dict) -> ClassifierProvider:
    c = (config or {}).get("classifier", {}) or {}
    provider = (c.get("provider") or "rule_based").lower()
    if provider == "api":
        return APIClassifier(api_url=c.get("api_url", ""), api_key=c.get("api_key", ""))
    return RuleBasedClassifier()
