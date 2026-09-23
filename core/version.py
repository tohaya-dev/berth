"""core.version — 版数の単一の出どころ (DD-CYN-0115 R-5)。

正本はリポジトリ直下の VERSION ファイル 1 か所 (決定 §52)。本モジュールはそれを
読むだけの葉で、何も import しないため循環しない。実行時に版が出る場所
(GET /api/health・/openapi.json・/docs) は全てここを読む。

姉妹系統の core/version.py は定数 1 つの形だが、berth は「正本 = VERSION・
他はそこから読む」の形を採る (本流は配布物を作らないため、ファイル同梱の前提が崩れない)。
MCP serverInfo の "2.0" は別軸の番号なので触らない (姉妹系統と同じ判断)。
公開しないため版番号は上げない。開発中の識別は日付と走行番号で行う。
"""

from pathlib import Path

_FALLBACK = "1.0.0-alpha"


def _read_version() -> str:
    try:
        vf = Path(__file__).resolve().parent.parent / "VERSION"
        for line in vf.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version:"):
                v = line.split(":", 1)[1].strip()
                return v[1:] if v.startswith("v") else v
    except Exception:
        pass
    return _FALLBACK


APP_VERSION = _read_version()

__all__ = ["APP_VERSION"]
