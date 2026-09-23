"""Cynovela 金庫暗号化 鍵窓口 (vault-enc)。

原本 (生本文) を SQLite / Chroma に保存する直前に通すための薄い窓口。
config.encrypt / config.decrypt (Fernet) を流用し、保存形式は ``"enc:" + base64`` で
冪等になるよう統一する。再発行ボタン等で鍵差替えする際は本モジュール 1 箇所で
吸収できる構造にしてある。

- ``enc_raw(t)`` : ``t`` が空または既に ``"enc:"`` 始まりならそのまま素通し、
  それ以外は ``"enc:" + config.encrypt(t)`` を返す。
- ``dec_raw(t)`` : ``t`` が ``"enc:"`` 始まりなら剥がして ``config.decrypt`` を呼び、
  それ以外 (masked 本文や旧来の平文を含む) はそのまま素通しする。

masked 行に ``dec_raw`` を掛けても無害に通過する設計のため、表面化箇所には
無条件で ``dec_raw`` を被せて取りこぼしを防ぐ運用を採る。
"""

from __future__ import annotations

import config as _config

__all__ = ["ENC_PREFIX", "enc_raw", "dec_raw"]

ENC_PREFIX = "enc:"


def enc_raw(text: str | None) -> str:
    """raw 本文を暗号化形式に揃える (冪等)。

    - None / 空文字: そのまま素通し (空文字を返す)
    - 既に ``"enc:"`` 始まり: 二重暗号化しない (そのまま返す)
    - それ以外: ``"enc:" + config.encrypt(text)`` を返す
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        # 防御的: bytes 等が来ても落ちないように str 化のみ
        try:
            text = str(text)
        except Exception:
            return ""
    if text == "":
        return ""
    if text.startswith(ENC_PREFIX):
        return text
    try:
        return ENC_PREFIX + _config.encrypt(text)
    except Exception:
        # 暗号化に失敗した場合は元文字列をそのまま返す (取り込み停止を避ける)。
        # 後段の dec_raw は ``enc:`` 始まりでない文字列を素通しするため整合する。
        return text


def dec_raw(text: str | None) -> str:
    """暗号化形式なら復号、それ以外 (masked / 旧平文) はそのまま素通し (冪等)。

    - None / 空文字: そのまま素通し (空文字を返す)
    - ``"enc:"`` 始まり: 剥がして ``config.decrypt`` を呼ぶ
    - それ以外: 何もせず返す (masked 本文・旧平文の双方をそのまま流せる)
    - 復号失敗時: 例外を握りつぶし元文字列を返す (検索/表示の総倒れを避ける)
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    if text == "":
        return ""
    if not text.startswith(ENC_PREFIX):
        return text
    try:
        return _config.decrypt(text[len(ENC_PREFIX):])
    except Exception:
        # 鍵不整合・破損などで復号に失敗した場合は ``enc:...`` のまま返す。
        # こうしておくと「復号できなかった」事実が UI でも検知できる。
        return text
