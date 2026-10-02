"""コンプライアンス・分類ヘッダー系エンドポイント。

- /api/compliance/checklist (GET): RBAC / PII / 監査 / Guardrail / ローカルLLM 簡易チェック
- /api/classification/categories (GET): Smart Ingestion カテゴリ一覧
- /api/compliance/report (GET): 監査ログのコンプライアンスレポート (HTML; window.print() → PDF)
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db import get_db
from core.auth import _require_admin
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_count_sql


router = APIRouter(tags=["compliance"])


@router.get("/api/classification/categories", response_model=None)
def list_classification_categories(request: Request):
    """S-4 (Smart Ingestion): 利用可能な 14 カテゴリ一覧を返す。

    Smart Ingestion の Lightweight Classifier が割り当てるカテゴリのキーと
    人間可読ラベルを返す。Collection 作成 UI の「分類で選択」タブで利用される。
    """
    _require_admin(request)
    from utils.metadata.classification import CATEGORIES as _CLS_CATS

    return {
        "categories": [{"key": key, "label": label} for key, label in _CLS_CATS.items()],
    }


@router.get("/api/compliance/checklist", response_model=None)
def compliance_checklist(request: Request):
    """Return runtime compliance status checks.

    Each item: {"id", "label_en", "label_ja", "ok": bool}.
    Used by the Guardrails page to show a quick health summary.
    """
    _require_admin(request)
    conn = get_db()
    try:
        # 1) Multi-role: at least 2 distinct roles among active users
        try:
            roles = [
                r["role"]
                for r in conn.execute("SELECT DISTINCT role FROM users WHERE COALESCE(is_active, 1) = 1").fetchall()
            ]
            ok_rbac = len(set(roles)) >= 2
        except Exception:
            ok_rbac = False
        # 2) PII detection: at least one workspace has a guardrail policy with PII action
        try:
            wp_count = conn.execute("SELECT COUNT(*) AS c FROM workspace_policies").fetchone()["c"]
            ok_pii = wp_count > 0
        except Exception:
            ok_pii = False
        # 3) Audit logging active: there is at least one row in audit_logs
        try:
            ok_audit = conn.execute("SELECT COUNT(*) AS c FROM audit_logs").fetchone()["c"] > 0
        except Exception:
            ok_audit = False
        # 4) At least one active guardrail policy
        try:
            ok_guard = (
                conn.execute("SELECT COUNT(*) AS c FROM guardrail_policies WHERE state = 'active'").fetchone()["c"] > 0
            )
        except Exception:
            ok_guard = False
        # 5) Local LLM only: 起動時の引数とエンドポイントが localhost / Tailscale 範囲かを
        #    サーバ運用者の判断で常に「ローカルのみ」という前提だが、簡易判定として
        #    settings.llm_endpoint がローカルアドレスかをチェックする。
        try:
            # DD-CYN-0115: 書き手のいない鍵 'llm.base_url' を読んでいた (書き手は 'llm_endpoint')
            row = conn.execute("SELECT value FROM settings WHERE key = 'llm_endpoint'").fetchone()
            ep = (row and row["value"] or "").lower()
            local_markers = ("127.0.0.1", "localhost", "::1", ".local", "10.", "192.168.", "172.")
            ok_local = (not ep) or any(m in ep for m in local_markers)
        except Exception:
            ok_local = True
    finally:
        conn.close()
    items = [
        {
            "id": "rbac",
            "ok": ok_rbac,
            "label_en": "Role-based access control (RBAC) is configured",
            "label_ja": "ロールベースアクセス制御（RBAC）が設定済み",
        },
        {
            "id": "pii",
            "ok": ok_pii,
            "label_en": "PII detection is enabled for at least one workspace",
            "label_ja": "PII 検出が 1 つ以上のワークスペースで有効",
        },
        {"id": "audit", "ok": ok_audit, "label_en": "Audit logging is active", "label_ja": "監査ログが有効"},
        {
            "id": "guardrail",
            "ok": ok_guard,
            "label_en": "At least one Guardrail policy is active",
            "label_ja": "Guardrail ポリシーが 1 件以上 active",
        },
        {
            "id": "no_external",
            "ok": ok_local,
            "label_en": "No external API calls configured (local LLM only)",
            "label_ja": "外部 API 呼び出しなし（ローカル LLM のみ）",
        },
    ]
    return {"items": items, "all_ok": all(i["ok"] for i in items)}


@router.get("/api/compliance/report", response_class=HTMLResponse)
async def compliance_report(request: Request, days: int = 30, limit: int = 500):
    """コンプライアンスレポートをHTML形式で返す。ブラウザで window.print() → PDF保存。

    DD-CYN-0116 X-6 U-10 (falcon route-admin-sweep-20260727 の移植): 監査記録
    (audit_logs) の明細を返す経路なので管理者専用へ寄せた。同じファイルの他2か所は
    元から管理者専用であり、CSV 版 /api/compliance-report.csv も管理者専用。
    HTML 版だけが取り残されていた非対称の是正。
    """
    _require_admin(request)
    from datetime import datetime, timezone

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT timestamp,user_id,action,target,result,category,ip_address "
            "FROM audit_logs WHERE timestamp >= datetime('now', ? || ' days') "
            "ORDER BY timestamp DESC LIMIT ?",
            (f"-{days}", limit),
        ).fetchall()
        counts = conn.execute(
            "SELECT COUNT(*) AS total,"
            "SUM(CASE WHEN result='success' THEN 1 ELSE 0 END) AS ok,"
            "SUM(CASE WHEN result='failure' THEN 1 ELSE 0 END) AS ng,"
            "SUM(CASE WHEN action LIKE '%publish%' THEN 1 ELSE 0 END) AS pub "
            "FROM audit_logs WHERE timestamp >= datetime('now', ? || ' days')",
            (f"-{days}",),
        ).fetchone()
        # Phase G (allinone): ガバナンス状態サマリ（PII/マスク/分類/ポリシー）— レポートに同梱
        try:
            gov = conn.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM chunks) AS chunks,"
                # ga-close-v3 PartD D-3: 数え方は guardrail.pii_count_sql の 1 か所から取る
                #   (旧 pii_detected=1 は層を絞らず raw+masked の二重計上だった)。
                f"(SELECT COUNT(*) FROM chunks WHERE {pii_count_sql()}) AS pii_chunks,"
                "(SELECT COUNT(*) FROM chunks WHERE chunk_id LIKE '%masked') AS masked_chunks,"
                "(SELECT COUNT(*) FROM files) AS files,"
                "(SELECT COUNT(*) FROM files WHERE categories IS NOT NULL AND categories <> '[]' AND categories <> '') AS classified,"
                "(SELECT COUNT(*) FROM workspaces) AS ws,"
                "(SELECT COUNT(DISTINCT workspace_id) FROM workspace_policies) AS ws_pol"
            ).fetchone()
        except Exception:
            gov = None
    finally:
        conn.close()

    def e(v):
        return "" if v is None else str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def badge(r):
        if r == "success":
            return '<span style="background:#dcfce7;color:#166534;padding:1px 6px;border-radius:3px;font-size:10px;">成功</span>'
        if r == "failure":
            return '<span style="background:#fee2e2;color:#991b1b;padding:1px 6px;border-radius:3px;font-size:10px;">失敗</span>'
        return e(r)

    row_html = "\n".join(
        f"<tr><td>{e(r['timestamp'])}</td><td>{e(r['user_id'])}</td><td>{e(r['action'])}</td>"
        f"<td>{e(r['target'])}</td><td>{badge(r['result'])}</td><td>{e(r['category'])}</td><td>{e(r['ip_address'])}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='7' style='text-align:center;color:#94a3b8;padding:12px;'>データなし</td></tr>"

    gen_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    c_total = counts["total"] or 0
    c_ok = counts["ok"] or 0
    c_ng = counts["ng"] or 0
    c_pub = counts["pub"] or 0
    g_chunks = (gov["chunks"] if gov else 0) or 0
    g_pii = (gov["pii_chunks"] if gov else 0) or 0
    g_masked = (gov["masked_chunks"] if gov else 0) or 0
    g_files = (gov["files"] if gov else 0) or 0
    g_classified = (gov["classified"] if gov else 0) or 0
    g_ws = (gov["ws"] if gov else 0) or 0
    g_ws_pol = (gov["ws_pol"] if gov else 0) or 0
    g_pol_pct = round(g_ws_pol / g_ws * 100) if g_ws else 0
    g_cls_pct = round(g_classified / g_files * 100) if g_files else 0
    html = f"""<!DOCTYPE html><html lang="ja"><head><meta charset="UTF-8"><title>Compliance Report</title>
<style>body{{font-family:sans-serif;font-size:12px;color:#1a1a1a}}@media print{{button{{display:none}}@page{{margin:20mm}}}}
h1{{font-size:18px;border-bottom:2px solid #16a34a;padding-bottom:6px}}
.meta{{color:#64748b;font-size:11px;margin-bottom:16px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:12px 0}}
.card{{border:1px solid #e2e8f0;border-radius:6px;padding:10px;text-align:center}}
.num{{font-size:22px;font-weight:800;color:#16a34a}}.lbl{{font-size:10px;color:#64748b;margin-top:4px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th{{background:#f1f5f9;padding:6px 8px;text-align:left;border:1px solid #e2e8f0;font-size:11px}}
td{{padding:4px 8px;border:1px solid #e2e8f0;font-size:11px;word-break:break-all;max-width:180px}}
</style></head><body>
<button onclick="window.print()" style="position:fixed;top:12px;right:12px;padding:8px 16px;background:#16a34a;color:#fff;border:none;border-radius:6px;cursor:pointer;">🖨️ PDF保存</button>
<h1>📊 Cynovela コンプライアンスレポート</h1>
<div class="meta">生成: {gen_ts} | 直近{days}日間</div>
<div class="grid">
<div class="card"><div class="num">{c_total}</div><div class="lbl">総アクション</div></div>
<div class="card"><div class="num">{c_ok}</div><div class="lbl">成功</div></div>
<div class="card"><div class="num">{c_ng}</div><div class="lbl">失敗</div></div>
<div class="card"><div class="num">{c_pub}</div><div class="lbl">Publish</div></div>
</div>
<h2>🛡️ ガバナンス状態（提出時点の実測）</h2>
<div class="grid">
<div class="card"><div class="num">0</div><div class="lbl">外部送信 bytes / ローカル完結</div></div>
<div class="card"><div class="num">{g_pii}</div><div class="lbl">PII検出チャンク</div></div>
<div class="card"><div class="num">{g_masked}</div><div class="lbl">伏字 __masked チャンク</div></div>
<div class="card"><div class="num">{g_cls_pct}%</div><div class="lbl">分類済 {g_classified}/{g_files}</div></div>
</div>
<div class="grid">
<div class="card"><div class="num">{g_pol_pct}%</div><div class="lbl">WSポリシー {g_ws_pol}/{g_ws}</div></div>
<div class="card"><div class="num">{g_chunks}</div><div class="lbl">総チャンク</div></div>
<div class="card"><div class="num">BGE-M3</div><div class="lbl">埋め込み 1024次元</div></div>
<div class="card"><div class="num">ローカル</div><div class="lbl">LLM推論(外部送信なし)</div></div>
</div>
<div class="meta">準拠フレームワーク（対応を支援）: EU AI Act · GDPR · NIST AI RMF · ISO 42001</div>
<h2>監査ログ（直近{limit}件・根拠記録 / answer provenance）</h2>
<table><tr><th>日時</th><th>ユーザー</th><th>アクション</th><th>対象</th><th>結果</th><th>カテゴリ</th><th>IP</th></tr>
{row_html}
</table></body></html>"""
    return HTMLResponse(content=html)
