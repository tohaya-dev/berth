"""Phase 2: worker 消費ループ（`server.py --mode worker` から起動）。

uvicorn を起動せず、Redis キュー（core.job_queue）を信頼配送で消費して、
取り込み/Publish を **アプリ内と同一の経路**で実行する:
  - Publish : server._run_publish_background(...) → rag.publish_collection_iter（伏字/暗号化を同一適用）
  - Scan    : server._do_scan(...)

進捗は publish_jobs（DB 正本）へ、停止/キャンセルは publish_jobs.cancel_requested を
ポーリングして in-process の停止 Event へ橋渡しする（worker から実 Publish を止められる）。

at-least-once:
  - ジョブ実行中は heartbeat スレッドが hb を打つ（DD-CYN-0115 H-1 以降は reserve 直後、
    引数の DB 再導出より前に開始する＝無防備区間を作らない）。
  - 正常/アプリ例外で終了したら ack（無限再試行しない）。
  - プロセスが途中で kill されると ack も hb も止まり、別 worker の reaper が再配送する。
    再配送は queue.max_redeliver 回まで（超えたら捨てて publish_jobs を failed にする）。

前提: provider 配線・config・DB は server.py __main__ が本関数呼び出し前に初期化済み。
新規 env は使わない（接続は yaml/DB settings 駆動）。
"""
from __future__ import annotations

import time
import json
import signal
import threading
import logging

logger = logging.getLogger("cynovela.worker")

# graceful drain: SIGTERM/SIGINT を受けたら「現ジョブを完了させてから」ループを抜ける。
# k8s の通常の Pod 削除（rolling update / node drain）は SIGTERM + grace period。
# 実行中の publish を途中で殺すと共有 SQLite を書込中断で壊し得るため、現ジョブ完走を保証する。
# （真のクラッシュ＝SIGKILL/電源断は Redis 信頼配送の reaper 再配送で別 worker が引き継ぐ。）
_shutdown = threading.Event()


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        logger.info(f"[worker] シグナル {signum} 受領。現ジョブ完了後に正常終了します（graceful drain）。")
        _shutdown.set()

    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _handler)
        except Exception:
            pass


def _hb_loop(job_id: str, interval: int, stop_evt: threading.Event) -> None:
    """処理中の生存証跡(heartbeat)を一定間隔で打つ。"""
    import core.job_queue as q

    while not stop_evt.is_set():
        q.heartbeat(job_id)
        stop_evt.wait(interval)


def _retryable_db_errors() -> tuple:
    """DD-CYN-0116 F-3: DB の切断系例外の組を返す（やり直してよい失敗の判定）。

    文字列一致では判定しない。standalone(SQLite) では psycopg が入らない場合が
    あるので、その時は空タプル＝やり直さない（従来どおりの振る舞い）へ落ちる。
    関数内 import はこのファイルの既存作法に合わせている。
    """
    try:
        import psycopg

        return (
            psycopg.OperationalError,
            psycopg.InterfaceError,
            psycopg.errors.AdminShutdown,
        )
    except Exception:
        return ()


def _cancel_watch(job_id: str, col_id: str, stop_evt: threading.Event) -> None:
    """publish_jobs.cancel_requested を監視し、立っていたら in-process 停止 Event を立てる。

    rag.publish_collection_iter は _publish_stop_flags[col_id] の Event を見て中断するため、
    ここで setdefault().set() しておけば worker 内の実 Publish を確実に止められる。
    """
    from db import get_db

    while not stop_evt.wait(2):
        try:
            conn = get_db()
            try:
                row = conn.execute(
                    "SELECT cancel_requested FROM publish_jobs WHERE id = ?", (job_id,)
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"[worker] cancel-watch DB エラー id={job_id}: {e}")
            continue
        if row and row["cancel_requested"]:
            try:
                import rag

                rag._publish_stop_flags.setdefault(col_id, threading.Event()).set()
                logger.info(f"[worker] cancel 受領 → 停止 Event を立てました job={job_id} col={col_id}")
            except Exception as e:
                logger.warning(f"[worker] cancel 橋渡し失敗: {e}")
            return


def _derive_publish_args(col_id: str):
    """col_id から publish に必要な引数を DB 再導出する（payload を軽量に保つ）。

    publish_async（routers/collections.py）と同一の取得方法。
    """
    import server
    from db import get_db

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT f.path FROM files f JOIN collection_files cf ON f.id = cf.file_id "
            "WHERE cf.collection_id = ?",
            (col_id,),
        ).fetchall()
        file_paths = [r["path"] for r in rows]
        excluded_paths = server.compute_exclude_paths_for_collection(conn, col_id)
        col = conn.execute("SELECT workspace_id FROM collections WHERE id = ?", (col_id,)).fetchone()
        ws_id = col["workspace_id"] if col else ""
        acl = conn.execute("SELECT acl_config FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
    finally:
        conn.close()
    pdf_mode = "fast"
    if acl and acl["acl_config"]:
        try:
            pdf_mode = (json.loads(acl["acl_config"]) or {}).get("pdf_mode") or "fast"
        except Exception:
            pdf_mode = "fast"
    cs, co = server._resolve_collection_chunking(col_id)
    return file_paths, excluded_paths, cs, co, pdf_mode


def _run_publish_job(job: dict, hb_interval: int) -> None:
    import server

    job_id = job["id"]
    col_id = job["col_id"]
    stop_evt = threading.Event()
    # DD-CYN-0115 H-1: hb は reserve 直後（_derive_publish_args より前）に始める。
    #   _derive_publish_args は DB を数回叩く（大きな Collection や Postgres の混雑時は
    #   秒単位でかかる）。従来はこの区間だけ hb を打つ者が居らず、reserve 時の1発だけを
    #   頼りに visibility_timeout を消費していた（無防備区間）。
    hb_t = threading.Thread(target=_hb_loop, args=(job_id, hb_interval, stop_evt), daemon=True)
    hb_t.start()
    try:
        file_paths, excluded_paths, cs, co, pdf_mode = _derive_publish_args(col_id)
        if not file_paths:
            from server import _update_publish_job

            _update_publish_job(
                job_id, status="failed", stage="error",
                message="Collectionにファイルがありません", error="no_files",
            )
            return
        cw_t = threading.Thread(target=_cancel_watch, args=(job_id, col_id, stop_evt), daemon=True)
        cw_t.start()
        # アプリ内（非同期 Publish）と同一の本体。伏字/暗号化は publish_collection_iter に一本化。
        server._run_publish_background(job_id, col_id, file_paths, excluded_paths, cs, co, pdf_mode)
    finally:
        stop_evt.set()


def _run_scan_job(job: dict, hb_interval: int) -> None:
    import server

    job_id = job["id"]
    source_id = job["source_id"]
    stop_evt = threading.Event()
    hb_t = threading.Thread(target=_hb_loop, args=(job_id, hb_interval, stop_evt), daemon=True)
    hb_t.start()
    try:
        # C-6: 排他クレームは _do_scan 内 (server._claim_scan) で行われる。
        # 既に走査中ならログを出してスキップされる (queue 経由の二重開始は開始しない)。
        server._do_scan(source_id)
    finally:
        stop_evt.set()


def _run_job(job: dict, hb_interval: int) -> None:
    t = job.get("type")
    if t == "publish":
        _run_publish_job(job, hb_interval)
    elif t == "scan":
        _run_scan_job(job, hb_interval)
    else:
        logger.warning(f"[worker] 未知の job type をスキップ: {t}")


# DD-CYN-0116: 受け取り手に見せてよい「こちらが意図して止めた」理由の印。
# これで始まる失敗だけは、そのまま画面へ出す (中身は判断の説明であって内部情報ではない)。
_USER_FACING_PREFIXES = ("取り込みを中止しました:",)


def _mark_failed(job: dict, msg: str) -> None:
    if job.get("type") == "publish" and job.get("id"):
        # 従来は理由を捨てて一律「内部エラー」にしていたため、こちらが意図して止めた
        # 場合でも受け取り手には理由の分からない失敗に見えていた
        # (例: 索引と埋め込みの食い違いで止めたとき)。意図した停止だけは理由を出す。
        _m = (msg or "").strip()
        _user_facing = any(_m.startswith(p) for p in _USER_FACING_PREFIXES)
        try:
            from server import _update_publish_job

            _update_publish_job(
                job["id"], status="failed", stage="error",
                message=(_m[:500] if _user_facing else "内部エラーが発生しました"),
                error=("stopped_on_purpose" if _user_facing else "worker_error"),
            )
        except Exception:
            pass


def run_worker_loop() -> None:
    """worker メインループ。SIGTERM/SIGINT で現ジョブ完了後に正常終了（graceful drain）。"""
    import core.job_queue as q

    _install_signal_handlers()
    cfg = q._qcfg()
    hb_interval = cfg["heartbeat_interval"]
    logger.info(f"[worker] 起動。queue url={cfg['url']} prefix={cfg['key_prefix']}")

    # Redis 疎通待ち（接続できるまでリトライ）
    while not _shutdown.is_set() and not q.ping():
        logger.warning("[worker] Redis 未接続。3秒後に再試行します...")
        time.sleep(3)
    if not _shutdown.is_set():
        logger.info(f"[worker] Redis 接続 OK。depths={q.queue_depths()}")

    while not _shutdown.is_set():
        try:
            q.reap_stale()
            # DD-CYN-0116 Q-3: Redis が落ちている間に受けた依頼は DB にだけ残る。
            # 復旧後にここで拾い直す（正本は publish_jobs 側の行）。
            try:
                q.sweep_orphan_jobs()
            except Exception as _e:
                logger.warning(f"[worker] 掃き寄せに失敗（続行）: {_e}")
            item = q.reserve()
            if item is None:
                continue
            raw, job = item
            jid = job.get("id")
            _rd = job.get("redeliver") or 0
            logger.info(
                f"[worker] 処理開始 type={job.get('type')} id={jid}"
                + (f" (再配送 {_rd} 回目)" if _rd else "")
            )
            try:
                try:
                    _run_job(job, hb_interval)
                except _retryable_db_errors() as e:
                    # DD-CYN-0116 F-3: DB の切断系はアプリの失敗ではない。
                    # Postgres を再起動した直後の1本目がこれを踏んで failed になり、
                    # 受け取り手には理由の分からない失敗に見えていた。
                    # プール側の生死検査 (relational_pg.py) で普段は発火しないが、
                    # 取り込みの途中で切れた場合の受け皿としてここで1回だけやり直す。
                    logger.warning(f"[worker] DB 切断を検知 id={jid}: {e} → 3秒後に1回やり直します")
                    time.sleep(3)
                    _run_job(job, hb_interval)
                logger.info(f"[worker] 処理完了 id={jid}")
            except Exception as e:
                logger.exception(f"[worker] ジョブ失敗 id={jid}: {e}")
                _mark_failed(job, str(e))
            finally:
                # アプリ例外は ack（無限再試行しない）。SIGKILL/電源断時は ack されず reaper が再配送。
                # やり直しは ack より内側で完結させる (ack を条件分岐にすると reaper の
                # 再配送と二重実行になる)。
                q.ack(raw, jid)
        except KeyboardInterrupt:
            logger.info("[worker] 停止要求を受領。終了します。")
            break
        except Exception as e:
            logger.exception(f"[worker] ループエラー: {e}")
            time.sleep(1)
    logger.info("[worker] graceful shutdown 完了（現ジョブ完了済み）。")
