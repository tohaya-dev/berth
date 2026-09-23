"""Dashboard summary endpoint (/api/dashboard/summary)."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Request

from db import get_db

import state as _state
from core.auth import _require_authenticated
# ga-close-v3 PartD D-3: 伏字件数の数え方は guardrail.py の 1 か所に集約する。
from guardrail import pii_counts_from_db

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard/summary", response_model=None)
async def dashboard_summary(request: Request):
    """BLOCK B-5 / P4-13: Overview画面用のダッシュボード集計データを返す。"""
    _require_authenticated(request)  # P0-2: Viewer もダッシュボード閲覧可 (403回避)
    conn = get_db()
    try:
        total_sources = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
        total_workspaces = conn.execute("SELECT COUNT(*) AS c FROM workspaces").fetchone()["c"]
        total_collections = conn.execute("SELECT COUNT(*) AS c FROM collections WHERE status='ready'").fetchone()["c"]
        # radar 保護軸(枝②): 公開(=伏字・保管済み)カバレッジの母数。全status件数を読み取るだけ(検出比は使わない=偽満点回避)。
        collections_total_all = conn.execute("SELECT COUNT(*) AS c FROM collections").fetchone()["c"]
        total_files = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        total_chunks = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()["c"]
        # zanken-fix1-20260706: raw/masked の dual-row 二重計上を是正（論理チャンク基準= raw 層のみ集計）
        # ga-close-v3 PartD D-3: 数え方は guardrail.pii_counts_from_db に集約した
        #   (pii_detected 列は伏字 0 件でも簡易正規表現の当たりで 1 になるため使わない)。
        _pii_counts = pii_counts_from_db(conn)
        pii_total = int(_pii_counts["pii_chunks"])
        excluded_chunks_total = conn.execute("SELECT COUNT(*) AS c FROM chunks WHERE excluded = 1").fetchone()["c"]
        try:
            today_q = conn.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE role='user' AND date(created_at) = date('now')"
            ).fetchone()["c"]
        except Exception:
            today_q = 0
        try:
            total_messages = conn.execute("SELECT COUNT(*) AS c FROM messages WHERE role='user'").fetchone()["c"]
        except Exception:
            total_messages = 0
        try:
            total_sessions = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
        except Exception:
            total_sessions = 0
        try:
            last_pub_row = conn.execute(
                "SELECT timestamp FROM publish_history ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            last_publish_at = last_pub_row["timestamp"] if last_pub_row else None
        except Exception:
            last_publish_at = None
        try:
            classified_files = conn.execute(
                "SELECT COUNT(*) AS c FROM files WHERE categories IS NOT NULL AND categories <> '[]' AND categories <> ''"
            ).fetchone()["c"]
        except Exception:
            classified_files = 0
        sensitivity_rows = conn.execute(
            "SELECT access_level, COUNT(*) AS c FROM collections GROUP BY access_level"
        ).fetchall()
        sensitivity_breakdown = {r["access_level"] or "unknown": r["c"] for r in sensitivity_rows}
        polling_ws = []
        try:
            for r in conn.execute(
                "SELECT id, name, sync_config FROM workspaces WHERE sync_config IS NOT NULL"
            ).fetchall():
                try:
                    cfg = json.loads(r["sync_config"]) if r["sync_config"] else {}
                except Exception:
                    cfg = {}
                if cfg.get("auto_poll"):
                    last = conn.execute(
                        "SELECT timestamp FROM audit_logs WHERE action='auto_scan_complete' "
                        "AND target = ? ORDER BY timestamp DESC LIMIT 1",
                        (r["id"],),
                    ).fetchone()
                    last_ts = last["timestamp"] if last else None
                    interval = int(cfg.get("poll_interval_seconds", 3600) or 3600)
                    next_at = None
                    if last_ts:
                        try:
                            from datetime import timedelta

                            dt = datetime.fromisoformat(last_ts)
                            next_at = (dt + timedelta(seconds=interval)).isoformat(timespec="seconds")
                        except Exception:
                            pass
                    polling_ws.append(
                        {
                            "workspace_id": r["id"],
                            "name": r["name"],
                            "interval_seconds": interval,
                            "auto_publish": bool(cfg.get("auto_publish", True)),
                            "last_scan_at": last_ts,
                            "next_scan_at": next_at,
                        }
                    )
        except Exception:
            pass
        recent_audit = [
            dict(r)
            for r in conn.execute(
                "SELECT id, timestamp, user_id, action, target, detail FROM audit_logs "
                "ORDER BY timestamp DESC LIMIT 10"
            ).fetchall()
        ]
    finally:
        conn.close()

    _app_config = _state.app_config_obj
    system_health = {
        "mode": getattr(_app_config, "mode", "?") if _app_config else "?",
        "demo": getattr(_app_config, "demo", False) if _app_config else False,
        "mock": getattr(_app_config, "mock", False) if _app_config else False,
    }

    try:
        from core.config import get_features as _gf

        features = _gf()
    except Exception:
        features = {}

    is_mock = bool(_state.config is not None and _state.config.mock)
    conn2 = get_db()
    try:
        ready_collections = conn2.execute(
            "SELECT COUNT(*) AS c FROM collections WHERE status = 'ready' AND archived_at IS NULL"
        ).fetchone()["c"]
        try:
            vectorized_chunks = conn2.execute("SELECT COUNT(*) AS c FROM chunks WHERE excluded = 0").fetchone()["c"]
        except Exception:
            vectorized_chunks = total_chunks
        ws_without_policy = 0
        try:
            ws_rows = conn2.execute(
                "SELECT id, guardrail_policy_id FROM workspaces WHERE archived_at IS NULL"
            ).fetchall()
            for w in ws_rows:
                row = conn2.execute(
                    "SELECT 1 FROM workspace_policies WHERE workspace_id = ? LIMIT 1",
                    (w["id"],),
                ).fetchone()
                has_policy = row is not None or bool(w["guardrail_policy_id"])
                if not has_policy:
                    ws_without_policy += 1
        except Exception:
            pass
        rag_basic_count = 0
        rag_agentic_count = 0
        zero_hit_count = 0
        # truth-fill Card②(検索スコア平均): 信頼度ゲートと同一基準(最上位 vector_score=cosine類似度)を
        #   既存 messages.retrieval_json.pipeline_detail.vector_scores から「読み取って」集計するだけ。
        #   検索/マスキング/暗号化ロジックは一切書き換えない・テキストは保存/読出ししない(数値のみ)。
        try:
            _thr_row = conn2.execute(
                "SELECT value FROM settings WHERE key='confidence_threshold'"
            ).fetchone()
            confidence_threshold = (
                float(_thr_row["value"]) if _thr_row and _thr_row["value"] not in (None, "") else 0.40
            )
        except Exception:
            confidence_threshold = 0.40
        retr_top_scores = []
        try:
            for r in conn2.execute(
                "SELECT retrieval_json FROM messages WHERE role='assistant' AND retrieval_json IS NOT NULL"
            ).fetchall():
                try:
                    from vault_enc import dec_raw as _dec_raw
                    rj = json.loads(_dec_raw(r["retrieval_json"])) if r["retrieval_json"] else {}
                    pd = rj.get("pipeline_detail", {}) or {}
                    if (pd.get("chunks_sent_to_llm") or 0) == 0 and (pd.get("total_chunks_searched") or 0) == 0:
                        zero_hit_count += 1
                    _vs = pd.get("vector_scores") or []
                    _vs = [float(x) for x in _vs if isinstance(x, (int, float))]
                    if _vs:
                        retr_top_scores.append(max(_vs))
                except Exception:
                    continue
        except Exception:
            pass
        _rn = len(retr_top_scores)
        retrieval_score_avg = (sum(retr_top_scores) / _rn) if _rn else None
        retrieval_below_threshold_rate = (
            (sum(1 for s in retr_top_scores if s < confidence_threshold) / _rn) if _rn else None
        )
        # truth-fill Card①(利用者評価/いいね集計): feedback テーブルの rating(+1/-1) のみを数値集計。
        #   質問文/回答文/PII は読まない(rating と件数のみ)。評価ゼロ→ null(フロントで N/A)。
        try:
            _fb = conn2.execute(
                "SELECT SUM(CASE WHEN rating=1 THEN 1 ELSE 0 END) AS up, "
                "SUM(CASE WHEN rating=-1 THEN 1 ELSE 0 END) AS down FROM feedback"
            ).fetchone()
            fb_up = (_fb["up"] or 0) if _fb else 0
            fb_down = (_fb["down"] or 0) if _fb else 0
        except Exception:
            fb_up = fb_down = 0
        fb_n = fb_up + fb_down
        user_feedback_rate = (fb_up / fb_n * 100.0) if fb_n else None
        try:
            from datetime import timedelta as _td

            yesterday = (datetime.now() - _td(hours=24)).isoformat(timespec="seconds")
            ingest_24h = conn2.execute(
                "SELECT COUNT(*) AS c FROM audit_logs "
                "WHERE timestamp >= ? AND action IN ('source_created','workspace_scan','file_added','collection_published')",
                (yesterday,),
            ).fetchone()["c"]
        except Exception:
            ingest_24h = 0
        # honest fix (pii-fiction): 実際に伏字したスパン総数(=実伏字件数)。masked tier の pii_summary {種別:件数} を合算。
        #   「PII未対処/レビュー待ち」(実体なし=pii_unreviewed)の置換表示用。検出比でなく実数・読み取りのみ。
        # ga-close-v3 PartD D-3: 集計は guardrail.pii_counts_from_db に集約した
        #   (層の指定と {種別:件数} の読み方をここで書き直さない)。
        try:
            masked_spans_total = int(pii_counts_from_db(conn2)["pii_spans"])
        except Exception:
            masked_spans_total = 0
        # rawmode-partD-20260709: raw_only=1(伏字なし取り込み)コレクションの存在数。
        #   raw_only コレクションは masked 層(tier='masked'行)を持たない設計のため、masked 由来集計
        #   (masked_spans_total 等)から物理的に外れる=「PII 0件のクリーン」に見える偽装を防ぐ表示用。
        #   応答フィールド追加のみで既存フィールドの意味・値は不変。collections.raw_only 列が無い
        #   旧DBでは except で 0(従来応答と同じ形・raw_only 不存在扱い)。
        raw_only_collections = 0
        try:
            raw_only_collections = conn2.execute(
                "SELECT COUNT(*) AS c FROM collections WHERE raw_only = 1"
            ).fetchone()["c"]
        except Exception:
            raw_only_collections = 0
    finally:
        conn2.close()

    # DEPRECATED(pii-fiction): 「レビュー」概念は実体なし。pii_total の無条件コピーで pii_unreviewed_count==pii_detections_total。
    #   LIVE 表示は masked_spans_total / 保護カバレッジ へ移行済。後方互換のためキーと値は据え置き(値は変えない)。
    pii_unreviewed = pii_total

    if is_mock:
        if ws_without_policy == 0 and total_workspaces > 1:
            ws_without_policy = max(0, total_workspaces - 1)
        if vectorized_chunks == 0 and total_chunks > 0:
            vectorized_chunks = total_chunks

    return {
        "total_sources": total_sources,
        "total_workspaces": total_workspaces,
        "total_collections": total_collections,
        "collections_total_all": collections_total_all,
        "ready_collections": ready_collections,
        "total_files": total_files,
        "total_chunks": total_chunks,
        "vectorized_chunks": vectorized_chunks,
        "pii_detections_total": pii_total,
        "pii_unreviewed_count": pii_unreviewed,  # DEPRECATED: 実体なし(=pii_detections_total)。masked_spans_total を使用。
        "masked_spans_total": masked_spans_total,
        # rawmode-partD-20260709: 伏字なし取り込み(raw_only)コレクション件数。masked由来数値の
        #   「対象外(伏字なし取り込み)」判定用。列不在の旧DBでは常に 0。
        "raw_only_collections": raw_only_collections,
        "ws_without_policy_count": ws_without_policy,
        "excluded_chunks_total": excluded_chunks_total,
        "total_queries_today": today_q,
        "total_messages": total_messages,
        "total_sessions": total_sessions,
        "last_publish_at": last_publish_at,
        "classified_files": classified_files,
        "sensitivity_breakdown": sensitivity_breakdown,
        "polling_workspaces": polling_ws,
        "recent_audit_events": recent_audit,
        "system_health": system_health,
        "features": features,
        "rag_basic_count": rag_basic_count,
        "rag_agentic_count": rag_agentic_count,
        "zero_hit_count": zero_hit_count,
        "ingest_24h": ingest_24h,
        # truth-fill: 2カード + しきい値カードの実数（n=0 は null → フロントで N/A 表示）
        "user_feedback_rate": user_feedback_rate,
        "user_feedback_n": fb_n,
        "retrieval_score_avg": retrieval_score_avg,
        "retrieval_below_threshold_rate": retrieval_below_threshold_rate,
        "retrieval_score_n": _rn,
        "confidence_threshold": confidence_threshold,
    }
