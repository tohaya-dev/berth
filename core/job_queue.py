"""Phase 2: Redis 信頼配送ジョブキュー（設定駆動・新規 env 非使用）。

接続/有効化は cynovela.yaml の `queue:` セクション（または DB settings の `exec.queue_*`）から読む。
標準構成では無効（queue.enabled=false）で、アプリは従来どおりプロセス内スレッドで動作する
（＝standalone は挙動不変）。worker 分離デプロイでは queue.enabled=true にし、API は enqueue、
worker（`server.py --mode worker`）が消費する。

信頼配送（at-least-once）:
  - enqueue : LPUSH  pending    <job_json>
  - reserve : BLMOVE pending -> processing（原子的に取得し processing へ退避）
              DD-CYN-0116 G-11: BRPOPLPUSH は Redis 6.2 で非推奨のため BLMOVE へ移した。
              あわせて processing:<処理役> にも印を残し、どの処理役が何を抱えたまま
              落ちたかを分かるようにした。
  - hb      : 処理中は HSET hb <job_id> <ts> を定期更新（worker 生存の証跡）
  - ack     : 完了で LREM processing <job_json> + HDEL hb <job_id>
  - reaper  : processing 内で hb が visibility_timeout を超えた項目を pending へ戻す
              （= worker クラッシュ時に別 worker が再処理 = redelivery）

Redis を AOF 永続化（appendonly yes）すれば pending/processing は Redis 再起動後も残る。

設計制約（指示書 §1）:
  - 新規 os.environ/getenv を増やさない。接続先は yaml/DB settings 経由のみ。
"""
from __future__ import annotations

import json
import time
import logging

logger = logging.getLogger("cynovela.job_queue")

_DEFAULTS = {
    "enabled": False,
    "url": "redis://127.0.0.1:6379/0",
    "key_prefix": "cynovela",
    # DD-CYN-0115 H-1: 60→300。実用サイズの資料は伏字/埋め込みで数分かかるため、
    #   短い期限は「まだ動いている worker のジョブ」を reaper が奪い、取り込みが
    #   永遠に完走しない循環（再配送→やり直し→また期限切れ）を生む。
    "visibility_timeout": 300,  # 秒。hb がこれを超えた processing は再配送
    "heartbeat_interval": 5,    # 秒。worker が hb を打つ間隔
    "reserve_timeout": 5,       # 秒。BLMOVE のブロック秒
    # DD-CYN-0115 H-1: 再配送の回数上限。これを超えたジョブは pending へ戻さず捨てる
    #   （毒ジョブの無限再配送を止める）。publish は publish_jobs を failed にしてから捨てる。
    "max_redeliver": 3,
}

_client = None  # redis.Redis（プロセス内キャッシュ）

# heartbeat の連続失敗を数える（job_id -> {"consec": int, "last_ok": float, "total": int}）。
# 黙って hb を欠落させると reaper に攫われる（=再配送）ため、失敗は必ず WARNING で残す。
_hb_state: dict = {}


def _qcfg() -> dict:
    """queue 設定を返す。優先順: DB settings(exec.queue_*) > cynovela.yaml(queue:) > 既定。

    新規 env は使わない。既存の CYNOVELA_CONFIG / get_execution_config() のみ参照する。
    """
    cfg = dict(_DEFAULTS)
    try:
        from core.config import CYNOVELA_CONFIG

        y = (CYNOVELA_CONFIG.get("queue") or {})
        for k in _DEFAULTS:
            if k in y and y[k] is not None:
                cfg[k] = y[k]
    except Exception:
        pass
    # DB settings(exec.queue_*) による実行時オーバーライド（既存名前空間を流用）
    try:
        from core.config import get_execution_config

        ec = get_execution_config()
        for k in _DEFAULTS:
            ek = f"queue_{k}"
            if ek in ec and ec[ek] not in (None, ""):
                cfg[k] = ec[ek]
    except Exception:
        pass
    # 型正規化
    cfg["enabled"] = str(cfg["enabled"]).lower() in ("1", "true", "yes", "on") if not isinstance(cfg["enabled"], bool) else cfg["enabled"]
    for k in ("visibility_timeout", "heartbeat_interval", "reserve_timeout", "max_redeliver"):
        try:
            cfg[k] = int(cfg[k])
        except Exception:
            cfg[k] = _DEFAULTS[k]
    return cfg


def queue_enabled() -> bool:
    return bool(_qcfg().get("enabled"))


def get_client():
    """redis クライアントを返す（プロセス内キャッシュ）。decode_responses=True。"""
    global _client
    if _client is not None:
        return _client
    import redis  # 遅延 import（standalone で redis 未導入でも import エラーにしない）

    url = _qcfg()["url"]
    _client = redis.Redis.from_url(url, decode_responses=True, socket_keepalive=True)
    return _client


def _keys():
    p = _qcfg()["key_prefix"]
    return (f"{p}:jobs:pending", f"{p}:jobs:processing", f"{p}:jobs:hb")


# ── DD-CYN-0116 G-11: 処理中の置き場を処理役ごとに分ける ────────────────
# 従来は全 worker が1本の processing を共有していた。∴ どの処理役が何を抱えたまま
# 落ちたのかが分からず、拾い直しの判断材料が hb の記録だけだった。
# 処理役ごとに分ければ「この処理役が抱えていた分」を名指しで拾える。
# 共有の processing も並行して維持する（reap の既存判定と掃き寄せがそのまま効く）。
_WORKER_ID: str | None = None


def worker_id() -> str:
    """この処理役の名前。K8s では Pod 名 (HOSTNAME) がそのまま使える。"""
    global _WORKER_ID
    if _WORKER_ID is None:
        import os as _os
        import uuid as _uuid

        _WORKER_ID = _os.environ.get("HOSTNAME") or f"worker-{_uuid.uuid4().hex[:8]}"
    return _WORKER_ID


def _worker_processing_key(wid: str | None = None) -> str:
    p = _qcfg()["key_prefix"]
    return f"{p}:jobs:processing:{wid or worker_id()}"


def worker_inflight(wid: str | None = None) -> list:
    """その処理役がいま抱えている依頼を返す (監視・後片づけ用)。"""
    try:
        return get_client().lrange(_worker_processing_key(wid), 0, -1) or []
    except Exception:
        return []


def worker_processing_map() -> dict:
    """どの処理役が何を抱えているかの一覧。落ちた処理役の特定に使う。"""
    p = _qcfg()["key_prefix"]
    out: dict = {}
    try:
        cli = get_client()
        for k in cli.scan_iter(match=f"{p}:jobs:processing:*", count=100):
            _wid = k.rsplit(":", 1)[-1]
            _ids = []
            for _raw in cli.lrange(k, 0, -1) or []:
                try:
                    _ids.append((json.loads(_raw) or {}).get("id"))
                except Exception:
                    continue
            out[_wid] = _ids
    except Exception as e:
        logger.warning(f"[queue] 処理役ごとの抱え込みを読めません: {e}")
    return out


def ping() -> bool:
    """Redis 疎通確認。"""
    try:
        return bool(get_client().ping())
    except Exception as e:
        logger.warning(f"[queue] ping 失敗: {e}")
        return False


def enqueue(job: dict) -> str:
    """ジョブを pending へ積む。job は 'id' を含むこと。raw(JSON) を返す。"""
    if "id" not in job:
        raise ValueError("job には 'id' が必要です")
    pending, _, _ = _keys()
    raw = json.dumps(job, ensure_ascii=False, sort_keys=True)
    get_client().lpush(pending, raw)
    logger.info(f"[queue] enqueue type={job.get('type')} id={job.get('id')}")
    return raw


def enqueue_or_defer(job: dict) -> bool:
    """DD-CYN-0116 Q-3: 積めたら True、Redis へ到達できなければ False を返す。

    呼び元は publish_jobs へ 'pending' 行を commit してからここへ来る。従来は
    lpush の例外がそのまま抜けて 500 になり、DB には誰も拾わない pending 行だけが
    永久に残っていた（受け取り手は「取り込んだつもりで何も起きない」）。

    正本は publish_jobs 側の行であって Redis ではない。∴ 積めなかった場合は
    False を返すだけにして行を残し、worker 側の掃き寄せ（sweep_orphan_jobs）が
    Redis 復旧後に拾い直す。
    """
    try:
        enqueue(job)
        return True
    except Exception as e:
        logger.warning(
            f"[queue] enqueue 失敗 id={job.get('id')}: {e} "
            "→ publish_jobs の pending 行を残し、worker の掃き寄せに委ねます"
        )
        return False


def sweep_orphan_jobs(max_items: int = 50) -> int:
    """DD-CYN-0116 Q-3: Redis に居ない 'pending' の publish_jobs を積み直す。

    Redis が落ちている間に受けた依頼は DB にだけ残る。復旧後、worker のループが
    これを呼んで拾い直す。二重実行を防ぐため、積む前に 'pending' → 'queued' の
    条件付き更新で1件を確保し、更新できた（rowcount==1）ものだけを積む。

    Returns: 積み直した件数
    """
    from db import get_db

    pending_key, processing_key, _ = _keys()
    try:
        cli = get_client()
        _in_redis = set(cli.lrange(pending_key, 0, -1) or []) | set(
            cli.lrange(processing_key, 0, -1) or []
        )
    except Exception as e:
        logger.warning(f"[queue] 掃き寄せ: Redis を読めません: {e}")
        return 0

    _known_ids = set()
    for _raw in _in_redis:
        try:
            _known_ids.add((json.loads(_raw) or {}).get("id"))
        except Exception:
            continue

    swept = 0
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, collection_id FROM publish_jobs WHERE status = 'pending' "
            "ORDER BY id LIMIT ?",
            (int(max_items),),
        ).fetchall()
        for r in rows:
            jid = r["id"]
            if jid in _known_ids:
                continue  # Redis に居る＝掃き寄せ不要
            cur = conn.execute(
                "UPDATE publish_jobs SET status = 'queued' WHERE id = ? AND status = 'pending'",
                (jid,),
            )
            if getattr(cur, "rowcount", 0) != 1:
                continue  # 別 worker が先に確保した
            conn.commit()
            try:
                enqueue({"id": jid, "type": "publish", "col_id": r["collection_id"]})
                swept += 1
            except Exception as e:
                # 積めなければ元へ戻す（次の掃き寄せでまた拾う）
                conn.execute(
                    "UPDATE publish_jobs SET status = 'pending' WHERE id = ? AND status = 'queued'",
                    (jid,),
                )
                conn.commit()
                logger.warning(f"[queue] 掃き寄せの積み直しに失敗 id={jid}: {e}")
                break
    finally:
        conn.close()
    if swept:
        logger.info(f"[queue] 掃き寄せ: Redis に居なかった依頼 {swept} 件を積み直しました")
    return swept


def reserve(timeout: int | None = None):
    """pending から1件を原子的に processing へ移して返す。

    返り値: (raw_str, job_dict) または None（timeout でブロック解除）。
    取得時に hb を打つ（直後に reaper に攫われないように）。
    """
    import redis.exceptions as _rexc

    pending, processing, hb = _keys()
    if timeout is None:
        timeout = _qcfg()["reserve_timeout"]
    cli = get_client()
    try:
        # DD-CYN-0116 G-11: BRPOPLPUSH は Redis 6.2 で非推奨。公式が勧める BLMOVE へ移す。
        # 向き RIGHT→LEFT は BRPOPLPUSH と同じ (先入れ先出しを保つ)。
        # 古い Redis に当たった場合だけ従来の命令へ退く。
        try:
            raw = cli.blmove(pending, processing, timeout, "RIGHT", "LEFT")
        except _rexc.ResponseError:
            raw = cli.brpoplpush(pending, processing, timeout=timeout)
    except _rexc.TimeoutError:
        # 空キューでブロックが解けただけ（job 無し）。例外でなく None として扱う。
        return None
    except _rexc.ConnectionError as e:
        logger.warning(f"[queue] reserve: Redis 接続エラー（再試行します）: {e}")
        time.sleep(1)
        return None
    if not raw:
        return None
    try:
        job = json.loads(raw)
    except Exception:
        # 壊れたペイロードは processing から除去して破棄
        cli.lrem(processing, 1, raw)
        logger.warning("[queue] 壊れた payload を破棄しました")
        return None
    jid = job.get("id")
    if jid:
        cli.hset(hb, jid, str(time.time()))
    # G-11: この処理役が抱えた印。落ちたときに「誰が何を抱えていたか」が残る。
    try:
        cli.lpush(_worker_processing_key(), raw)
    except Exception as e:
        logger.warning(f"[queue] 処理役ごとの印を付けられません (続行): {e}")
    return (raw, job)


def heartbeat(job_id: str) -> bool:
    """処理中の生存証跡を更新する。成功なら True。

    DD-CYN-0115 H-1: 従来は例外を1行 WARNING にするだけで、連続何回落ちているか
    （＝あと何秒で reaper に攫われるか）が分からなかった。ここで連続失敗を数え、
    「最後に成功してから visibility_timeout を超えた」時点で別種の WARNING を出す。
    再接続そのものは get_client() のキャッシュと redis-py 側の作りに従う（本関数は
    クライアントを作り直さない = 既存挙動を変えない）。
    """
    _, _, hb = _keys()
    now = time.time()
    try:
        get_client().hset(hb, job_id, str(now))
    except Exception as e:
        st = _hb_state.get(job_id) or {"consec": 0, "last_ok": now, "total": 0}
        st["consec"] += 1
        st["total"] += 1
        _hb_state[job_id] = st
        gap = now - st["last_ok"]
        logger.warning(
            f"[queue] heartbeat 失敗 id={job_id} 連続{st['consec']}回 "
            f"最後の成功から{gap:.1f}秒: {e}"
        )
        try:
            vis = _qcfg()["visibility_timeout"]
        except Exception:
            vis = _DEFAULTS["visibility_timeout"]
        if gap > vis:
            logger.warning(
                f"[queue] heartbeat 欠落が visibility_timeout({vis}秒)を超えました id={job_id}。"
                "処理は続いていますが、他 worker の reaper が再配送する可能性があります。"
            )
        return False
    st = _hb_state.get(job_id)
    if st and st.get("consec"):
        logger.warning(
            f"[queue] heartbeat 回復 id={job_id}（連続失敗 {st['consec']} 回のあと成功）"
        )
    _hb_state[job_id] = {"consec": 0, "last_ok": now, "total": (st or {}).get("total", 0)}
    return True


def heartbeat_forget(job_id: str) -> None:
    """終了したジョブの hb 失敗カウンタを捨てる（長寿 worker のメモリ滞留を防ぐ）。"""
    _hb_state.pop(job_id, None)


def ack(raw: str, job_id: str | None = None) -> None:
    """完了したジョブを processing から除去する（再配送対象から外す）。"""
    _, processing, hb = _keys()
    cli = get_client()
    try:
        cli.lrem(processing, 1, raw)
        # G-11: 処理役ごとの印も外す (抱え込みの一覧が実態と合うように)
        cli.lrem(_worker_processing_key(), 1, raw)
        if job_id:
            cli.hdel(hb, job_id)
    except Exception as e:
        logger.warning(f"[queue] ack 失敗 id={job_id}: {e}")
    finally:
        if job_id:
            heartbeat_forget(job_id)


def _parse_db_utc(v) -> float | None:
    """DB の UTC 文字列（'YYYY-MM-DD HH:MM:SS'）を epoch 秒へ。解せなければ None。

    SQLite の datetime('now') も Postgres シム（to_char(... at time zone 'utc')）も
    同じ書式の TEXT を返す（publish_jobs.updated_at は両バックエンドとも TEXT）。
    """
    from datetime import datetime, timezone

    if v is None:
        return None
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    s = str(v).strip().replace("T", " ")
    if s.endswith("Z"):
        s = s[:-1]
    s = s.split(".")[0].split("+")[0].strip()
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return dt.timestamp()


def _publish_progress_age(job_id: str) -> float | None:
    """publish_jobs.updated_at の経過秒を DB の時計基準で返す。取れなければ None。

    DD-CYN-0115 H-1: hb（Redis）だけを判定材料にすると、worker が生きて実際に進んで
    いても hb スレッドが詰まった/Redis が一時的に落ちた瞬間に reaper が仕事を奪う。
    実進捗の正本は publish_jobs（DB）なので、そこを第二の判定材料にする。
    now も同じ SELECT で DB から取り、worker Pod と DB の時計ずれを判定に混ぜない。
    server 側のヘルパは import せず、worker と同じ db.get_db()（sqlite/pg 両対応）だけを使う。
    """
    conn = None
    try:
        from db import get_db

        conn = get_db()
        row = conn.execute(
            "SELECT updated_at, datetime('now') AS now_utc FROM publish_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    except Exception as e:
        logger.warning(f"[queue] reap: publish_jobs 参照に失敗（hb 判定のみで続行） id={job_id}: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    if not row:
        return None
    upd = _parse_db_utc(row["updated_at"])
    nowdb = _parse_db_utc(row["now_utc"])
    if upd is None or nowdb is None:
        return None
    return max(0.0, nowdb - upd)


def _fail_publish_job(job_id: str, attempts: int) -> None:
    """再配送上限を超えた publish ジョブを DB 上で failed にする（捨てる前の後始末）。"""
    conn = None
    try:
        from db import get_db

        conn = get_db()
        conn.execute(
            "UPDATE publish_jobs SET status = 'failed', stage = 'error', "
            "message = ?, error = 'redeliver_limit', updated_at = datetime('now') "
            "WHERE id = ?",
            (f"取り込みを完了できませんでした（再試行の上限 {attempts - 1} 回に達しました）", job_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"[queue] reap: publish_jobs の failed 記録に失敗 id={job_id}: {e}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def reap_stale() -> int:
    """processing 内で hb が visibility_timeout を超えた項目を pending へ戻す。

    worker クラッシュ（hb 停止）時に別 worker が再処理できるようにする中核。
    戻り値: 再配送した件数。

    DD-CYN-0115 H-1 で足した判定は2つ:
      (1) 第二判定 — publish で publish_jobs.updated_at が新しい（期限の2倍以内）なら
          hb が切れていても reap しない。実際に進んでいる取り込みを奪わない。
      (2) 再配送上限 — payload の 'redeliver' を数え、上限を超えたら pending へ戻さず捨てる。
          publish は捨てる前に publish_jobs を failed にする（画面が pending のまま残らない）。
    """
    pending, processing, hb = _keys()
    cli = get_client()
    cfg = _qcfg()
    vis = cfg["visibility_timeout"]
    maxr = cfg["max_redeliver"]
    now = time.time()
    requeued = 0
    try:
        items = cli.lrange(processing, 0, -1)
    except Exception as e:
        logger.warning(f"[queue] reap: lrange 失敗: {e}")
        return 0
    for raw in items or []:
        try:
            job = json.loads(raw)
        except Exception:
            cli.lrem(processing, 1, raw)
            continue
        jid = job.get("id")
        ts = cli.hget(hb, jid) if jid else None
        try:
            stale = (ts is None) or ((now - float(ts)) > vis)
        except Exception:
            stale = True
        if not stale:
            continue
        # (1) 第二判定: hb が切れていても DB 側の実進捗が新しければ奪わない。
        if job.get("type") == "publish" and jid:
            age = _publish_progress_age(jid)
            if age is not None and age <= vis * 2:
                logger.warning(
                    f"[queue] reap 見送り: hb は期限切れだが DB の進捗が新しい id={jid} "
                    f"（updated_at {age:.0f}秒前 ≤ {vis * 2}秒）。hb 経路の不調を疑うこと。"
                )
                continue
        # LREM してから LPUSH（原子性は単一 worker reaper 前提で十分）
        removed = cli.lrem(processing, 1, raw)
        if not removed:
            continue
        # G-11: 落ちた処理役の印も外す (どの処理役の分だったかはログへ残す)
        try:
            _owner = None
            for _wid, _ids in worker_processing_map().items():
                if jid in _ids:
                    _owner = _wid
                    break
            if _owner:
                cli.lrem(_worker_processing_key(_owner), 1, raw)
                logger.warning(f"[queue] reap: 処理役 {_owner} が抱えたまま落ちた分です id={jid}")
        except Exception:
            pass
        if jid:
            cli.hdel(hb, jid)
        # (2) 再配送上限: 上限超過は pending へ戻さず捨てる（毒ジョブの無限再配送を止める）。
        attempts = 0
        try:
            attempts = int(job.get("redeliver") or 0)
        except Exception:
            attempts = 0
        attempts += 1
        if attempts > maxr:
            logger.warning(
                f"[queue] reap: 再配送上限({maxr}回)を超えたため破棄 type={job.get('type')} id={jid}"
            )
            if job.get("type") == "publish" and jid:
                _fail_publish_job(jid, attempts)
            continue
        job["redeliver"] = attempts
        newraw = json.dumps(job, ensure_ascii=False, sort_keys=True)
        cli.lpush(pending, newraw)
        requeued += 1
        logger.warning(
            f"[queue] reap: 再配送 type={job.get('type')} id={jid} ({attempts}/{maxr}回目)"
        )
    return requeued


def queue_depths() -> dict:
    """監視用: pending/processing の件数を返す。"""
    pending, processing, _ = _keys()
    cli = get_client()
    try:
        return {"pending": cli.llen(pending), "processing": cli.llen(processing)}
    except Exception:
        return {"pending": -1, "processing": -1}
