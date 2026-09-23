"""
日本語/英語対応トークナイザー

PHASE 3 BM25 の前処理に使用。
日本語が含まれる場合は fugashi (MeCab/UniDic) で形態素解析、
英語のみの場合はスペース区切り＋記号除去。
"""

from __future__ import annotations

import re
from typing import List


_JA_RE = re.compile(r"[ぁ-んァ-ン一-龯々]")
_NON_WORD = re.compile(r"[^\w\s]")

# BM25 のスコアを汚染する日本語助詞・助動詞・補助動詞を除外する
# (fugashi の品詞タグを使わず surface で除外する軽量実装)
_JA_STOPWORDS = frozenset(
    {
        "です",
        "ます",
        "ある",
        "あり",
        "する",
        "した",
        "して",
        "いる",
        "いう",
        "なる",
        "れる",
        "られ",
        "こと",
        "もの",
        "ため",
        "これ",
        "それ",
        "あれ",
        "ここ",
        "そこ",
        "あそこ",
        "から",
        "まで",
        "より",
        "など",
        "のみ",
        "だけ",
        "ほど",
        "くらい",
        "ぐらい",
        "そして",
        "また",
        "しかし",
        "ただし",
        "または",
        "あるいは",
        "つまり",
        "なお",
    }
)

# fugashi の Tagger は構築コストが高いため、モジュール単位でキャッシュする
_TAGGER = None
_TAGGER_INIT_TRIED = False


def _get_tagger():
    """fugashi.Tagger() を遅延初期化。失敗した場合は None を返す。"""
    global _TAGGER, _TAGGER_INIT_TRIED
    if _TAGGER is not None:
        return _TAGGER
    if _TAGGER_INIT_TRIED:
        return None
    _TAGGER_INIT_TRIED = True
    try:
        import fugashi  # type: ignore

        _TAGGER = fugashi.Tagger()
    except Exception:
        _TAGGER = None
    return _TAGGER


def tokenize(text: str) -> List[str]:
    """日本語が含まれる場合は形態素解析、英語のみの場合は単純分割。

    1 文字以下のトークン (助詞・記号など) はノイズとして除外する。
    """
    if not text:
        return []
    has_japanese = bool(_JA_RE.search(text))

    if has_japanese:
        tagger = _get_tagger()
        if tagger is not None:
            try:
                tokens = [w.surface for w in tagger(text) if w.surface and w.surface.strip()]
                # 英語混在時のケース不一致を防ぐため小文字化、日本語ストップワードを除外
                return [t.lower() for t in tokens if len(t) > 1 and t not in _JA_STOPWORDS]
            except Exception:
                pass
        # フォールバック: 文字 N-gram (2 文字) を生成して BM25 に渡す
        clean = re.sub(r"\s+", "", text).lower()
        return [clean[i : i + 2] for i in range(len(clean) - 1) if len(clean[i : i + 2]) == 2]

    # 英語: 小文字化 + 記号除去 + スペース分割 + 1 文字以下除外
    s = text.lower()
    s = _NON_WORD.sub(" ", s)
    return [t for t in s.split() if len(t) > 1]
