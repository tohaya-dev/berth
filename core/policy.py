"""Phase G: OPA / Rego 最小ポリシー評価。

`policies/cynovela.rego`（role=viewer かつ sensitivity=high → deny）を OPA で評価する。
opa バイナリが見つかれば `opa eval` で本物の Rego を評価し、無ければ同一規則の純 Python
フォールバックで評価する（規則の正本は .rego）。新規 os.environ/getenv は使わない。

接続/パスは cynovela.yaml `policy` セクション（opa_binary）から。秘密は扱わない。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REGO = os.path.join(_APP_DIR, "policies", "cynovela.rego")


def _opa_binary() -> str:
    """opa バイナリを探す: yaml policy.opa_binary > PATH > 既定候補。無ければ ''。"""
    try:
        from core.config import CYNOVELA_CONFIG as _DTC

        cfg = (_DTC.get("policy") or {}).get("opa_binary") or ""
        if cfg and os.path.exists(cfg):
            return cfg
    except Exception:
        pass
    w = shutil.which("opa")
    if w:
        return w
    for cand in ("/tmp/phase1/opa", os.path.join(_APP_DIR, "store", "bin", "opa")):
        if os.path.exists(cand):
            return cand
    return ""


def _eval_opa(opa: str, inp: dict) -> dict | None:
    """opa eval で allow / deny_reason を取る。失敗時 None。"""
    try:
        r = subprocess.run(
            [opa, "eval", "-d", _REGO, "-I", "--format", "json", "data.cynovela.authz"],
            input=json.dumps(inp),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return None
        out = json.loads(r.stdout)
        val = out["result"][0]["expressions"][0]["value"]
        return {"allow": bool(val.get("allow", True)), "deny_reason": val.get("deny_reason", ""), "engine": "opa"}
    except Exception:
        return None


def _eval_python(inp: dict) -> dict:
    """純 Python フォールバック（.rego と同一規則）。"""
    role = (inp or {}).get("role")
    sens = (inp or {}).get("sensitivity")
    if role == "viewer" and sens == "high":
        return {"allow": False, "deny_reason": "viewer は high 機微度ドキュメントの出力/エクスポートを許可されていません", "engine": "python-fallback"}
    return {"allow": True, "deny_reason": "", "engine": "python-fallback"}


def evaluate(role: str, sensitivity: str, action: str = "export") -> dict:
    """ポリシー評価。{allow, deny_reason, engine} を返す。"""
    inp = {"role": role, "sensitivity": sensitivity, "action": action}
    opa = _opa_binary()
    if opa:
        res = _eval_opa(opa, inp)
        if res is not None:
            return res
    return _eval_python(inp)


def is_allowed(role: str, sensitivity: str, action: str = "export") -> bool:
    return bool(evaluate(role, sensitivity, action).get("allow", True))
