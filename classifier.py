import re
import os

# Built-in category definitions
CATEGORIES = {
    "PII": {
        "patterns": [
            r"\b[\w.-]+@[\w.-]+\.\w+\b",  # Email
            r"\b0[789]0-?\d{4}-?\d{4}\b",  # Mobile phone
            r"\b0\d{1,4}-?\d{1,4}-?\d{4}\b",  # Landline
            r"\b\d{3}-?\d{4}-?\d{4}\b",  # My Number (simplified)
            r'https?://[^\s<>"　]+',  # #04: URL (内部URLも個人情報扱い)
        ],
        "keywords": ["個人情報", "氏名", "住所", "生年月日", "マイナンバー", "social security", "personal data"],
    },
    "Financial": {
        "patterns": [r"\b¥[\d,]+\b", r"\$[\d,]+", r"売上|revenue|profit|budget|予算|決算"],
        "keywords": ["財務", "売上", "決算", "予算", "revenue", "profit", "financial", "budget", "invoice"],
    },
    "HR": {
        "patterns": [],
        "keywords": [
            "人事",
            "採用",
            "給与",
            "退職",
            "evaluation",
            "salary",
            "hiring",
            "termination",
            "employee contract",
        ],
    },
    "Legal": {
        "patterns": [],
        "keywords": ["契約", "法務", "規約", "コンプライアンス", "contract", "legal", "compliance", "NDA", "agreement"],
    },
    "Healthcare": {
        "patterns": [],
        "keywords": ["医療", "健康", "診断", "処方", "health", "medical", "diagnosis", "prescription"],
    },
    "Sales": {
        "patterns": [],
        "keywords": ["営業", "提案", "見積", "案件", "sales", "proposal", "quotation", "deal", "pipeline"],
    },
    "Technical": {
        "patterns": [],
        "keywords": [
            "技術",
            "設計",
            "仕様",
            "API",
            "アーキテクチャ",
            "technical",
            "specification",
            "architecture",
            "engineering",
        ],
    },
    "Marketing": {
        "patterns": [],
        "keywords": ["マーケティング", "広告", "キャンペーン", "marketing", "campaign", "advertising", "branding"],
    },
}


def classify_text(text: str) -> list[str]:
    """Analyze text and return list of matching categories."""
    text_lower = text.lower()
    found = []
    for cat, rules in CATEGORIES.items():
        # Pattern match
        matched = False
        for pat in rules["patterns"]:
            if re.search(pat, text, re.IGNORECASE):
                found.append(cat)
                matched = True
                break
        if not matched:
            # Keyword match
            for kw in rules["keywords"]:
                if kw.lower() in text_lower:
                    found.append(cat)
                    break
    return list(set(found))


def classify_file(file_path: str, extracted_text: str) -> list[str]:
    """Classify from file path and extracted text. File name is also considered."""
    categories = classify_text(extracted_text)
    fname = os.path.basename(file_path).lower()
    name_cats = classify_text(fname)
    return list(set(categories + name_cats))


# ============================================================
# P5 BLOCK-B: Metadata Engine — ルールベース自動分類（LLM不使用）
# ============================================================

# 文書種別のキーワードルール
DOC_TYPE_RULES = [
    ("contract", ["契約", "合意", "規約", "agreement", "contract", "nda"]),
    ("proposal", ["提案", "プロポーザル", "proposal", "quotation", "見積"]),
    ("report", ["レポート", "報告", "report", "summary", "白書"]),
    ("policy", ["ポリシー", "規程", "規則", "policy", "guideline", "manual"]),
    ("specification", ["仕様", "spec", "設計書", "architecture", "design doc"]),
    ("financial_doc", ["決算", "財務諸表", "balance sheet", "p&l", "invoice", "請求書"]),
    ("personnel_doc", ["人事", "採用", "給与", "personnel", "salary", "evaluation", "履歴書"]),
    ("meeting_minutes", ["議事録", "minutes", "meeting note", "memo"]),
    ("presentation", ["プレゼン", "presentation", "deck", "pitch"]),
]

# 感度ルール（強い順）
SENSITIVITY_RULES = [
    (
        "restricted",
        1.0,
        [
            "極秘",
            "機密",
            "social security",
            "passport",
            "クレジットカード",
            "credit card number",
            "ssn",
            "ナンバー",
            "credentials",
            "password",
        ],
    ),
    (
        "confidential",
        0.7,
        [
            "confidential",
            "internal use only",
            "社外秘",
            "業務秘密",
            "給与",
            "salary",
            "personnel",
            "個人情報",
            "personal data",
            "決算",
            "financial statement",
            "売上",
        ],
    ),
    (
        "internal",
        0.4,
        [
            "internal",
            "社内",
            "業務",
            "提案",
            "proposal",
            "internal memo",
        ],
    ),
]

# 部門推定
DEPARTMENT_RULES = [
    ("Sales", ["sales", "営業", "案件", "deal", "pipeline"]),
    ("Finance", ["finance", "財務", "経理", "決算", "invoice", "請求"]),
    ("HR", ["hr", "人事", "採用", "給与", "personnel"]),
    ("Engineering", ["engineering", "engineer", "エンジニア", "技術", "spec", "architecture"]),
    ("Legal", ["legal", "法務", "契約", "compliance", "コンプライアンス"]),
    ("Marketing", ["marketing", "マーケティング", "campaign", "広告"]),
]


def detect_doc_type(text: str, filename: str = "") -> str:
    """文書種別をキーワードから推定する。該当なしは 'general'。"""
    hay = (text or "")[:5000].lower() + " " + (filename or "").lower()
    for key, kws in DOC_TYPE_RULES:
        for kw in kws:
            if kw.lower() in hay:
                return key
    return "general"


def detect_sensitivity(text: str, filename: str = "") -> tuple[str, float]:
    """感度ラベルとスコア(0..1) を返す。"""
    hay = (text or "")[:5000].lower() + " " + (filename or "").lower()
    for label, score, kws in SENSITIVITY_RULES:
        for kw in kws:
            if kw.lower() in hay:
                return (label, score)
    return ("public", 0.0)


def detect_department(text: str, filename: str = "") -> str:
    """部門タグを推定する。該当なしは ''。"""
    hay = (text or "")[:5000].lower() + " " + (filename or "").lower()
    for dept, kws in DEPARTMENT_RULES:
        for kw in kws:
            if kw.lower() in hay:
                return dept
    return ""


def detect_owner(text: str, filename: str = "") -> str:
    """ファイル名・本文先頭からオーナー名（[Author] / Owner: パターン）を抽出する。"""
    if not text:
        return ""
    head = text[:2000]
    m = re.search(r"(?:owner|担当者|作成者|author)\s*[:：]\s*([^\n,;]{1,40})", head, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def auto_tags_for(text: str, filename: str = "") -> list[str]:
    """既存 categories に加え、補助タグを抽出する。"""
    out: list[str] = []
    if re.search(r"\bdraft\b|ドラフト|草案", (text or "")[:2000], re.IGNORECASE):
        out.append("draft")
    if re.search(r"\bfinal\b|確定版|最終版", (text or "")[:2000], re.IGNORECASE):
        out.append("final")
    if re.search(r"\bv\d+\.\d+", (filename or "")):
        out.append("versioned")
    return out


def classify_metadata(file_path: str, extracted_text: str) -> dict:
    """P5-B: ファイル単位のメタデータ一括推定（doc_type/sensitivity/department/auto_tags/owner）。"""
    fname = os.path.basename(file_path or "")
    doc_type = detect_doc_type(extracted_text, fname)
    label, score = detect_sensitivity(extracted_text, fname)
    department = detect_department(extracted_text, fname)
    owner = detect_owner(extracted_text, fname)
    tags = auto_tags_for(extracted_text, fname)
    return {
        "doc_type": doc_type,
        "sensitivity": label,
        "sensitivity_score": float(score),
        "department": department,
        "owner": owner,
        "auto_tags": tags,
    }
