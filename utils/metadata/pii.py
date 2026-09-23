"""utils.metadata.pii — PII 検出 / マスク / 感度スコア (実体集約版)。

Stage R6-fix (Phase 3-fix): 旧 utils/pii_detector.py の実装を本ファイルに移植。
presidio が使えれば使う、ダメなら正規表現フォールバック。日本語・英語両対応。

Stage R7 C-5 で `llm_judge_pi` を追加し、HIGH 5 LLM judge ベースの PI 検出を提供する。

【設計方針】
- presidio が使えれば使う。使えなければ正規表現フォールバック
- 日本語・英語両対応
- 全例外をキャッチして動作継続 (Publish を止めない)
- 感度スコアを 0〜100 で返す (0:公開 / 25:社内 / 50:機密 / 75:極秘)
- P4: GiNZA NER Recognizer / 日本語住所ルール / 検出モード切替
"""

from __future__ import annotations

import re
from typing import Dict, List

# 正規表現フォールバック (presidio 不在時)
FALLBACK_PATTERNS = {
    "EMAIL": r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    "PHONE_JP": r"0\d{1,4}[-\s]?\d{1,4}[-\s]?\d{4}",
    "PHONE_INTL": r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
    "IP_ADDRESS": r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
    "MY_NUMBER": r"\d{4}[\s\-]?\d{4}[\s\-]?\d{4}",
    "CREDIT_CARD": r"\b(?:\d{4}[\s\-]?){4}\b",
    # fix061 B4: INTERNAL_URL マスク。プライベート IP / localhost / 内部ホスト名つき URL を検出。
    "INTERNAL_URL": r"https?://(?:localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|[a-zA-Z0-9\-]+\.(?:local|internal|corp|lan|intra))(?::\d+)?(?:/[^\s]*)?",
}

HIGH_RISK_TYPES = {"CREDIT_CARD", "MY_NUMBER", "SSN", "PASSPORT", "IBAN_CODE"}

# P4: 検出モード（lite / standard / quality）
PII_DETECTION_MODE = "standard"


def set_pii_detection_mode(mode: str) -> None:
    """検出モードを設定。lite=最小・standard=GiNZA NER含む・quality=全機能"""
    global PII_DETECTION_MODE, _ANALYZER, _ANALYZER_INIT_TRIED
    if mode not in ("lite", "standard", "quality"):
        return
    if mode != PII_DETECTION_MODE:
        _ANALYZER = None
        _ANALYZER_INIT_TRIED = False
    PII_DETECTION_MODE = mode


def get_pii_detection_mode() -> str:
    return PII_DETECTION_MODE


def get_active_recognizers() -> list:
    """起動モードに応じて有効な entity 一覧を返す"""
    base = ["EMAIL_ADDRESS", "PHONE_NUMBER", "DATE_TIME"]
    if PII_DETECTION_MODE == "lite":
        return base
    return base + ["PERSON_JP", "ORG_JP", "LOC_JP", "ADDRESS_JP"]


# presidio キャッシュ
_ANALYZER = None
_ANALYZER_INIT_TRIED = False


# A6 fix (2026-06-04): presidio EntityRecognizer をモジュール先頭で取り込む。
# presidio 不在時は object を基底にして import 失敗を避け、regex フォールバックを温存する。
try:
    from presidio_analyzer import EntityRecognizer as _EntityRecognizerBase
except Exception:  # presidio 不在
    _EntityRecognizerBase = object


class GinzaNerRecognizer(_EntityRecognizerBase):
    """GiNZA を使った日本語人名認識 (PERSON_JP)。

    A6 fix (2026-06-04): 旧実装は presidio ``EntityRecognizer`` を継承しておらず、
    ``add_recognizer`` が ``ValueError: Input is not of type EntityRecognizer`` を
    投げて握り潰され、日本語人名検出が ``ja_core_news_sm`` 単独に落ちていた。その
    結果、実在名 (中村美咲 等) を取りこぼし、技術・製品語 (アグリゲート / storage-vendor 等)
    を PERSON 過剰検出していた。本実装は EntityRecognizer を正しく継承し、GiNZA
    (split_mode="C") の Person 判定のうち、形態素タグに「人名」を含む span のみ
    PERSON_JP として採用する。これにより普通名詞 (送信元 / オンプレミス 等) や英字
    トークン (Amazon / Google 等) の誤検出を構造的に排除する。住所/組織は ADDRESS_JP
    パターン側に委ね、人名は GiNZA に一本化する。
    """

    ENTITIES = ["PERSON_JP"]

    def __init__(self):
        self._nlp = None
        self._available = False
        try:
            import spacy

            self._nlp = spacy.load(
                "ja_ginza",
                config={"components": {"compound_splitter": {"split_mode": "C"}}},
            )
            self._available = True
        except Exception:
            self._available = False

        if _EntityRecognizerBase is not object:
            super().__init__(
                supported_entities=self.ENTITIES,
                supported_language="ja",
                name="GinzaNerRecognizer",
            )
        else:
            self.supported_entities = self.ENTITIES
            self.supported_language = "ja"
            self.name = "GinzaNerRecognizer"

    def load(self) -> None:
        pass

    def get_supported_entities(self) -> list:
        return self.ENTITIES

    def analyze(self, text, entities=None, nlp_artifacts=None):
        if not self._available or not text:
            return []
        try:
            from presidio_analyzer import RecognizerResult

            results = []
            for ent in self._nlp(text).ents:
                # GiNZA の人名ラベル かつ 形態素タグ「人名」(固有名詞-人名-姓/名) を
                # 含む span のみ採用。普通名詞・英字トークンの過剰マスクを構造的に防ぐ。
                if ent.label_ != "Person":
                    continue
                if not any("人名" in tok.tag_ for tok in ent):
                    continue
                if entities and "PERSON_JP" not in entities:
                    continue
                results.append(
                    RecognizerResult(
                        entity_type="PERSON_JP",
                        start=ent.start_char,
                        end=ent.end_char,
                        score=0.85,
                    )
                )
            return results
        except Exception:
            return []


def _get_analyzer():
    global _ANALYZER, _ANALYZER_INIT_TRIED
    if _ANALYZER is not None or _ANALYZER_INIT_TRIED:
        return _ANALYZER
    _ANALYZER_INIT_TRIED = True
    try:
        from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        cfg = {
            "nlp_engine_name": "spacy",
            "models": [
                {"lang_code": "en", "model_name": "en_core_web_sm"},
                {"lang_code": "ja", "model_name": "ja_core_news_sm"},
            ],
        }
        provider = NlpEngineProvider(nlp_configuration=cfg)
        analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())

        if PII_DETECTION_MODE in ("standard", "quality"):
            try:
                analyzer.registry.add_recognizer(GinzaNerRecognizer())
            except Exception as _ginza_err:
                # A6 fix: 旧コードは握り潰していた。登録失敗は人名NER無効化に直結する
                # ため、必ずログに残す (黙殺禁止)。
                import logging

                logging.getLogger(__name__).warning(
                    "GinzaNerRecognizer 登録失敗 (日本語人名NER無効化): %s", _ginza_err
                )
            # A6 fix: ja_core_news_sm 由来の PERSON 過剰検出を排除し、日本語人名は
            # GinzaNerRecognizer(PERSON_JP) に一本化する。en SpacyRecognizer は温存。
            try:
                analyzer.registry.recognizers = [
                    _r
                    for _r in analyzer.registry.recognizers
                    if not (
                        type(_r).__name__ == "SpacyRecognizer"
                        and getattr(_r, "supported_language", None) == "ja"
                    )
                ]
            except Exception as _rm_err:
                import logging

                logging.getLogger(__name__).warning(
                    "ja SpacyRecognizer 除去失敗: %s", _rm_err
                )
            try:
                jp_address_patterns = [
                    # ADDRESS_JP誤検出 fix: 旧 `〒?\d{3}-\d{4}` は携帯/固定電話の前半
                    # (例 090-1111, 03-1234 内の 234-5678) を郵便番号として誤検出し、
                    # NER が regex PHONE より先に走るため _publish_mask_text で
                    # "[ADDRESS_JP:***]-2222" のように電話番号末尾が素通りしていた。
                    # 前後がダッシュ/数字でない (= より長い数字連結の一部でない) 場合のみ
                    # 郵便番号として採用し、電話番号形状の数字列は regex PHONE_JP に委ねる。
                    Pattern("JP_POSTAL", r"(?<![\d\-])〒?\d{3}-\d{4}(?![\d\-])", 0.85),
                    # §F4 (2026-06-21 住所延伸): 旧パターンは [市区町村] までしか掴まず
                    # 「東京都千代田区丸の内1-2-3 サンプルビル5階」の番地・建物が素通りしていた。
                    # [市区町村] の後ろに「町名+番地(1-2-3)＋任意の建物名(空白始まり・ビル/館等で終端)」
                    # を *任意グループ* で追加。グループは optional のため番地の無い住所は従来と同一挙動。
                    # 建物は先頭が半角/全角スペース必須＝改行や「契約番号:」等の後続を飲み込まない。
                    Pattern(
                        "JP_PREF",
                        r"(北海道|東京都|大阪府|京都府|[^\s]{2,3}[県])[\w\s]+[市区町村]"
                        r"([^\s\d、。]{0,10}\d{1,4}([-－]\d{1,4}){1,3}"
                        r"([ 　][^\s\d、。]{1,15}?(ビル|館|タワー|ハイツ|マンション|号館)(\d{1,3}階)?)?)?",
                        0.70,
                    ),
                ]
                jp_address_recognizer = PatternRecognizer(
                    supported_entity="ADDRESS_JP",
                    patterns=jp_address_patterns,
                    supported_language="ja",
                )
                analyzer.registry.add_recognizer(jp_address_recognizer)
            except Exception:
                pass

        _ANALYZER = analyzer
    except Exception:
        _ANALYZER = None
    return _ANALYZER


def detect_pii(text: str, lang: str = "ja") -> List[Dict]:
    """presidio を試行、失敗時は正規表現フォールバック"""
    if not text:
        return []
    if PII_DETECTION_MODE == "lite":
        return _detect_with_regex(text)
    try:
        analyzer = _get_analyzer()
        if analyzer is not None:
            results = analyzer.analyze(
                text=text,
                language=lang if lang in ("en", "ja") else "en",
            )
            return [
                {
                    "type": r.entity_type,
                    "start": r.start,
                    "end": r.end,
                    "text": text[r.start : r.end],
                    "score": float(r.score),
                }
                for r in results
            ]
    except Exception:
        pass
    return _detect_with_regex(text)


def _detect_with_regex(text: str) -> List[Dict]:
    results: List[Dict] = []
    for pii_type, pattern in FALLBACK_PATTERNS.items():
        for m in re.finditer(pattern, text):
            results.append(
                {
                    "type": pii_type,
                    "start": m.start(),
                    "end": m.end(),
                    "text": m.group(),
                    "score": 0.8,
                }
            )
    return results


def mask_pii(text: str, pii_list: List[Dict] = None) -> str:
    """PII を [TYPE:***] でマスク。pii_list 省略時は detect_pii を呼ぶ。"""
    if pii_list is None:
        pii_list = detect_pii(text)
    for pii in sorted(pii_list, key=lambda x: x["start"], reverse=True):
        text = text[: pii["start"]] + f"[{pii['type']}:***]" + text[pii["end"] :]
    return text


def get_sensitivity_score(pii_list: List[Dict]) -> int:
    """感度スコア (0〜100) を返す"""
    if not pii_list:
        return 0
    if any(p["type"] in HIGH_RISK_TYPES for p in pii_list):
        return min(75 + len(pii_list) * 5, 100)
    if len(pii_list) >= 5:
        return 50
    if len(pii_list) >= 2:
        return 25
    return 15


def get_sensitivity_label(score: int, lang: str = "ja") -> str:
    labels = {
        "ja": [(75, "極秘"), (50, "機密"), (25, "社内"), (0, "公開")],
        "en": [(75, "Secret"), (50, "Confidential"), (25, "Internal"), (0, "Public")],
    }
    for threshold, label in labels.get(lang, labels["en"]):
        if score >= threshold:
            return label
    return "Public"


# ============================================================
# Stage R7 C-5: LLM judge ベースの PI 検出 (HIGH-5)
# ============================================================


def llm_judge_pi(text: str, provider: str = "lmstudio") -> Dict:
    """LLM judge による prompt injection / PII 判定。

    Stage R7 C-5 で追加。cynovela.yaml の `llm.provider` に従う。
    返り値:
        {
            "is_pi": bool,       # prompt injection 検出
            "is_pii": bool,      # PII 検出
            "confidence": float, # 0.0-1.0
            "reason": str,       # 判定理由 (LLM 出力)
        }

    実装: provider 抽象化 (LM Studio / Ollama 切替)、失敗時は False 返却 (動作継続)。
    """
    if not text:
        return {"is_pi": False, "is_pii": False, "confidence": 0.0, "reason": ""}

    try:
        from core.config import CYNOVELA_CONFIG

        llm_cfg = CYNOVELA_CONFIG.get("llm") or {}
        base_url = llm_cfg.get("base_url") or "http://localhost:1234"
        model = llm_cfg.get("model") or ""

        prompt = (
            "次のテキストにプロンプトインジェクション攻撃またはPII (個人情報) が含まれているか判定してください。\n"
            'JSON 形式で {"is_pi": true/false, "is_pii": true/false, "confidence": 0.0-1.0, "reason": "..."} を返してください。\n\n'
            f"テキスト: {text[:1000]}"
        )

        import json

        import httpx

        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 200,
                },
            )
            if response.status_code != 200:
                return {"is_pi": False, "is_pii": False, "confidence": 0.0, "reason": "llm_error"}

            data = response.json()
            content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")

            try:
                start = content.find("{")
                end = content.rfind("}") + 1
                if start >= 0 and end > start:
                    parsed = json.loads(content[start:end])
                    return {
                        "is_pi": bool(parsed.get("is_pi", False)),
                        "is_pii": bool(parsed.get("is_pii", False)),
                        "confidence": float(parsed.get("confidence", 0.0)),
                        "reason": str(parsed.get("reason", ""))[:500],
                    }
            except Exception:
                pass

    except Exception:
        pass

    return {"is_pi": False, "is_pii": False, "confidence": 0.0, "reason": "exception"}


__all__ = [
    "FALLBACK_PATTERNS",
    "HIGH_RISK_TYPES",
    "detect_pii",
    "get_active_recognizers",
    "get_pii_detection_mode",
    "get_sensitivity_label",
    "get_sensitivity_score",
    "llm_judge_pi",
    "mask_pii",
    "set_pii_detection_mode",
]
