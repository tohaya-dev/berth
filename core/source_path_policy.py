"""取り込みパスの禁止規則 (scheme / 区切り文字 / `..` / 機密パス) を1か所に持つ。

q3b-fix-20260922: 従来この規則は routers/sources.py (POST /api/sources) と
routers/files.py (POST /api/folder-scan-preview) に逐語で二重に書かれ、
routers/chat.py (POST /api/workspaces/import) には無かった。ここへ寄せて、
新しい入口が「規則の写し」ではなく同じ関数を呼ぶようにする。

役割分担:
  - ここ: どの取り込み元の下であっても断る場所 (/etc, ~/.ssh など) と、
          パスとして受け付けない書き方 (URL scheme, `\\`, `..`) の判定。
  - routers/sources.py の _assert_source_path_in_ingest_roots: 取り込み元 (根) の
    集合という境界の判定。境界の仕組みはそちら1つだけを使い、ここでは持たない。

注意: routers/sources.py 側の写しは今夜は他の担当が触るため据え置き。
      次にそちらを触るときに check_source_path_policy へ置き換える。
"""

from __future__ import annotations

import os
import re

from fastapi import HTTPException


FORBIDDEN_SCHEMES = ("file://", "data://", "ftp://", "javascript:")

FORBIDDEN_PREFIXES = (
    "/etc",
    "/var/root",
    "/var/db",
    "/private/etc",
    "/private/var/root",
    "/root",
    "/sys",
    "/proc",
    "/boot",
)

FORBIDDEN_SUBSTRINGS = (
    "/.ssh",
    "/.aws",
    "/.gnupg",
    "/Library/Keychains",
    "/Library/Application Support/com.apple.sharedfilelist",
    "/.kube",
    "/.config/gh",
    "/.netrc",
)


def _collapse_leading_slashes(path: str) -> str:
    """POSIX の normpath は先頭の `//` を (規格上の意味があるため) 残す。

    D4: `//etc` は normpath 後も `//etc` のままで、`/etc` との前方一致を擦り抜けていた。
    禁止規則の比較に限って先頭の連続スラッシュを1本に畳む (保存値は変えない)。
    """
    return re.sub(r"^/+", "/", path)


def forbidden_reason(path: str) -> str | None:
    """path が禁止場所に当たるなら理由の文言を、当たらなければ None を返す。

    比較は正規化 (normpath) 済みの値か realpath 済みの値に対して行う想定。
    """
    _p = _collapse_leading_slashes(path)
    if any(_p == p or _p.startswith(p + "/") for p in FORBIDDEN_PREFIXES):
        return f"system path is not allowed: {path}"
    if any(s in _p for s in FORBIDDEN_SUBSTRINGS):
        return f"sensitive path is not allowed: {path}"
    return None


def check_source_path_policy(raw_path: str, *, field: str = "path") -> str:
    """POST /api/sources と同じ入口検査。通れば normpath(expanduser) 済みの値を返す。

    順序も文言も routers/sources.py の create_source に揃える (テストが文言を見る)。
    境界 (取り込み元の根) の判定はここでは行わない。呼ぶ側が続けて
    routers.sources._assert_source_path_in_ingest_roots を呼ぶこと。
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise HTTPException(400, f"{field} is required")
    _lower = raw_path.strip().lower()
    if any(_lower.startswith(s) for s in FORBIDDEN_SCHEMES):
        raise HTTPException(400, f"URL scheme is not allowed in {field}")
    if "\\" in raw_path:
        raise HTTPException(400, "Windows path separator is not allowed")
    if ".." in raw_path.split(os.sep):
        raise HTTPException(400, "relative path traversal is not allowed")
    _normalized = os.path.normpath(os.path.expanduser(raw_path))
    _why = forbidden_reason(_normalized)
    if _why:
        raise HTTPException(400, _why)
    return _normalized


def assert_realpath_not_forbidden(real_path: str) -> str:
    """境界判定が返した実体パス (realpath) にも禁止規則を掛ける。

    D4: 正規化前の値だけを見ると `//etc` や、根の下から symlink で禁止場所へ抜ける
    パスを見逃す。realpath 後の値で同じ規則をもう一度通す。通れば同じ値を返す。
    """
    _why = forbidden_reason(real_path)
    if _why:
        raise HTTPException(400, _why)
    return real_path
