"""Phase 3: 関係層 Postgres バックエンド（SQLite 互換シム）。

db.py の `RelationalBackend` を実装し、`cynovela.yaml database.backend=postgres` の時に
`get_relational_backend().connect()` が返す接続を Postgres に切替える。呼び出し側 258 点は
sqlite3.Connection の作法（`conn.execute(sql, params)` / `?` プレースホルダ / `sqlite3.Row` /
`cur.lastrowid` / `executescript` / 明示 commit）を変えない＝単一トランク。

方言差は本ファイル内で吸収する（手書き正規表現置換はしない・SQL を理解する sqlglot AST 変換）:
  - `?` → `%s`（sqlglot postgres 方言が変換）
  - `datetime('now'[, mod])` / `date('now')` → `to_char((now() at time zone 'utc')[+ (mod)::interval], fmt)`
  - `LIKE` → `ILIKE`（SQLite 既定の大小無視に合わせる）
  - `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (<pk>) DO UPDATE SET <全列>=EXCLUDED`（REPLACE の
    「省略列は既定値へ」を全列 EXCLUDED 更新で等価再現）。`INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`。
  - `PRAGMA` → 特別扱い（read は擬似行・set/checkpoint は no-op）
  - `lastrowid`：IDENTITY 表への INSERT は `RETURNING <pk>` を付け、cursor.lastrowid に返す。
変換結果は文単位でキャッシュ（同一 SQL 文字列→変換済み）。接続は psycopg_pool で 1 プール共有。
接続情報は cynovela.yaml `database.postgres.*`（新規 os.environ/getenv は使わない）。
"""
from __future__ import annotations

import logging
import re
import threading

import sqlglot
from sqlglot import exp

from db import RelationalBackend
from providers.pgvector_store import _resolve_pg_params  # yaml database.postgres 解決を流用（env 非使用）

logger = logging.getLogger("cynovela.relational_pg")

# IDENTITY（INTEGER PK AUTOINCREMENT）表 → pk 列名（P0.5-7 実測）。
# INSERT 時に RETURNING <pk> を付けて lastrowid を提供する対象。
_IDENTITY_PK = {
    "file_hashes": "id",
    "publish_history": "id",
    "feedback": "id",
    "processing_logs": "id",
    "message_rag_refs": "id",
}

# 変換キャッシュ（同一 SQL 文字列 → (kind, translated_sql, returning_pk)）
_XLATE_CACHE: dict[str, tuple] = {}
_XLATE_LOCK = threading.Lock()

# PRAGMA table_info(<table>) 検出（shim-fix-20260719 Part2）。table 名は引用符/角括弧を許容。
_PRAGMA_TABLE_INFO_RE = re.compile(
    r"^PRAGMA\s+table_info\s*\(\s*(?P<t>[^)]+?)\s*\)\s*;?\s*$", re.IGNORECASE
)


# ─── SQL 変換（sqlglot AST） ───
def _dt_to_char(node):
    """datetime('now'[, mod]) / date('now') を Postgres to_char(...) 式へ置換する AST 変換。"""
    name = None
    if isinstance(node, exp.Anonymous):
        name = str(node.name).upper()
        args = list(node.expressions or [])
    elif isinstance(node, exp.Date):
        # date('now') 等：sqlglot は exp.Date(this=...) で表す
        name = "DATE"
        args = [node.this] if node.this is not None else []
        args += list(node.expressions or [])
    else:
        return node
    if name not in ("DATETIME", "DATE"):
        return node
    if not args:
        return node
    a0 = args[0]
    fmt = "YYYY-MM-DD HH24:MI:SS" if name == "DATETIME" else "YYYY-MM-DD"
    if isinstance(a0, exp.Literal) and str(a0.this).lower() == "now":
        # date('now') / datetime('now'[, mod]) → 現在時刻(UTC)基準
        base = sqlglot.parse_one("(now() AT TIME ZONE 'utc')", read="postgres")
    else:
        # shim-fix-20260719 Part1: date(列) / datetime(列) も to_char 化する。
        #   SQLite の date(text)/datetime(text) は ISO 文字列を YYYY-MM-DD[ HH24:MI:SS] 文字列へ正規化する。
        #   従来は 'now' 以外を素通ししていたため、PG では date(created_at) が date 型に解決され、
        #   text 側（date('now')→to_char）との比較で "operator does not exist: date = text" を誘発し、
        #   その失敗が握りつぶされて接続を汚し dashboard 等が連鎖 500 になっていた（事実84・本ランで是正）。
        #   両辺を to_char テキストへ揃えることで text=text 比較として成立させる。
        #   列は ISO テキスト格納（created_at 既定は to_char(...)）のため ::timestamptz キャストで解釈する。
        base = exp.Cast(this=exp.Paren(this=a0.copy()), to=exp.DataType.build("timestamptz"))
    inner = base
    mods = args[1:]
    if mods:
        # SQLite の修飾子（'-30 days' / param / '? || '' days''）を interval 加算で再現。
        # 複数修飾子は本コードベースに無い（P0.5-3）。1 つを (mod)::interval として加算。
        m = mods[0].copy()
        cast = exp.Cast(this=exp.Paren(this=m), to=exp.DataType.build("interval"))
        inner = exp.Add(this=base, expression=cast)
        for extra in mods[1:]:
            cast2 = exp.Cast(this=exp.Paren(this=extra.copy()), to=exp.DataType.build("interval"))
            inner = exp.Add(this=inner, expression=cast2)
    return exp.func("to_char", inner, exp.Literal.string(fmt))


def _like_to_ilike(node):
    """SQLite の LIKE（ASCII 大小無視既定）を Postgres ILIKE に合わせる。"""
    if isinstance(node, exp.Like):
        return exp.ILike(this=node.this, expression=node.expression)
    return node


def _json_extract_to_pg(node):
    """latent-fix-20260719: SQLite json_extract(col, '$.a[.b…]') を Postgres で成立させる。

    sqlglot は json_extract を JSON_EXTRACT_PATH(text, key) へ落とすが、PG には
    text を受ける json_extract_path が無く "function json_extract_path(text, unknown)
    does not exist" になる（/api/stats/* ・/api/guardrails/pii-detections の 500 の実因）。
    ここでは `CASE WHEN pg_input_is_valid(col,'jsonb') THEN (col)::jsonb #>> '{a,b}' END`
    へ置換する。#>> はテキストを返すため、SQLite json_extract の「文字列/数値として
    比較・CAST される」用法（AVG(CAST(... AS REAL)) / = 'positive' / IS NOT NULL /
    COALESCE(..., detail)）がそのまま成立する。pg_input_is_valid ゲート(PG16+)は
    audit_logs.detail に JSON でないフリーテキストが混在するため（routers/guardrails.py:41
    のコメントで確認済みの実データ性質）。malformed は NULL になり、SQLite 側の
    json_valid() フィルタ運用と同じ「拾わない」側へ倒れる。"""
    if not isinstance(node, exp.JSONExtract):
        return node
    path = node.expression
    keys: list[str] = []
    if isinstance(path, exp.JSONPath):
        for part in path.expressions:
            if isinstance(part, exp.JSONPathRoot):
                continue
            if isinstance(part, exp.JSONPathKey) and isinstance(part.this, str):
                keys.append(part.this)
            else:
                return node  # 添字等の未知形は触らない（黙って誤変換しない）
    else:
        return node
    if not keys:
        return node
    col_sql = node.this.sql(dialect="postgres")
    keys_lit = "{" + ",".join(keys) + "}"
    return sqlglot.parse_one(
        f"CASE WHEN pg_input_is_valid({col_sql}, 'jsonb') "
        f"THEN CAST({col_sql} AS JSONB) #>> '{keys_lit}' ELSE NULL END",
        read="postgres",
    )


def _json_valid_to_pg(node):
    """latent-fix-20260719: SQLite json_valid(x) を pg_input_is_valid(x,'jsonb') の 1/0 へ。"""
    if isinstance(node, exp.Anonymous) and str(node.name).upper() == "JSON_VALID":
        args = list(node.expressions or [])
        if len(args) == 1:
            col_sql = args[0].sql(dialect="postgres")
            return sqlglot.parse_one(
                f"(CASE WHEN pg_input_is_valid({col_sql}, 'jsonb') THEN 1 ELSE 0 END)",
                read="postgres",
            )
    return node


def _typed_placeholder_is(node):
    """latent-fix-20260719: `? IS [NOT] NULL` の裸プレースホルダに TEXT キャストを付ける。

    PG は素の $1 の型を推論できず "could not determine data type of parameter $1"
    になる（/api/workspaces/selectable の 500 の実因）。NULL 判定は型に依存しないため
    CAST(? AS TEXT) は SQLite の挙動と等価。"""
    if isinstance(node, exp.Is) and isinstance(node.this, exp.Placeholder):
        node.set("this", exp.Cast(this=node.this.copy(), to=exp.DataType.build("text")))
    return node


def _all_xform(node):
    node = _dt_to_char(node)
    node = _like_to_ilike(node)
    node = _json_extract_to_pg(node)
    node = _json_valid_to_pg(node)
    node = _typed_placeholder_is(node)
    return node


def _translate(sql: str) -> tuple:
    """SQLite SQL → (kind, translated_sql, returning_pk) を返す。文単位キャッシュ。

    kind: 'sql' | 'pragma_noop' | 'pragma_read_journal' | 'pragma_read_fk'
    returning_pk: IDENTITY 表 INSERT で付与した pk 列名（lastrowid 取得用）／無ければ None
    """
    cached = _XLATE_CACHE.get(sql)
    if cached is not None:
        return cached
    stripped = sql.strip()
    upper = stripped.upper()
    result: tuple
    if upper.startswith("PRAGMA"):
        _ti = _PRAGMA_TABLE_INFO_RE.match(stripped)
        if "JOURNAL_MODE" in upper and "=" not in stripped:
            result = ("pragma_read_journal", "", None)
        elif "FOREIGN_KEYS" in upper and "=" not in stripped:
            result = ("pragma_read_fk", "", None)
        elif _ti is not None:
            # shim-fix-20260719 Part2: PRAGMA table_info(T) を information_schema 参照へ翻訳する。
            #   従来は他 PRAGMA と一括で no-op 化され「列0件」を返していたため、列存在確認
            #   （routers/collections.py:271 の raw_only ゲート等）が PG では常に「列なし」と誤判定し、
            #   raw_only Collection 作成が追加済みの列を見つけられず 400 で止まっていた（事実84・本ランで是正）。
            #   SQLite の PRAGMA table_info と同じ 6 列 (cid,name,type,notnull,dflt_value,pk) を
            #   同順・同 index で返し、呼び出し側（r[1]=name を参照）が挙動不変で通るようにする。
            tname = (_ti.group("t") or "").strip().strip("'\"[]`")
            tname_lit = tname.replace("'", "''")
            translated = (
                "SELECT (c.ordinal_position - 1) AS cid, c.column_name AS name, "
                "c.data_type AS type, "
                "(CASE WHEN c.is_nullable = 'NO' THEN 1 ELSE 0 END) AS notnull, "
                "c.column_default AS dflt_value, "
                "(CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END) AS pk "
                "FROM information_schema.columns c "
                "LEFT JOIN ("
                "  SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "  JOIN information_schema.key_column_usage kcu "
                "    ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema "
                f"  WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = '{tname_lit}' "
                "    AND tc.table_schema = current_schema()"
                ") pk ON pk.column_name = c.column_name "
                f"WHERE c.table_name = '{tname_lit}' AND c.table_schema = current_schema() "
                "ORDER BY c.ordinal_position"
            )
            result = ("sql", translated, None)
        else:
            # set/checkpoint/その他 PRAGMA は Postgres で no-op
            result = ("pragma_noop", "", None)
        with _XLATE_LOCK:
            _XLATE_CACHE[sql] = result
        return result
    try:
        parsed = sqlglot.parse_one(sql, read="sqlite")
    except Exception as e:
        # パース不能は素通し（接続側で psycopg がエラーにする）。?→%s だけは保証できないため警告。
        logger.warning(f"[pg] sqlglot parse 失敗（素通し）: {e}: {sql[:120]}")
        result = ("sql", sql, None)
        with _XLATE_LOCK:
            _XLATE_CACHE[sql] = result
        return result

    returning_pk = None
    # INSERT OR REPLACE / OR IGNORE の処理
    if isinstance(parsed, exp.Insert):
        alt = (parsed.args.get("alternative") or "")
        alt = str(alt).upper() if alt else ""
        ignore = bool(parsed.args.get("ignore"))
        table = _insert_table_name(parsed)
        if alt == "REPLACE":
            parsed = _rewrite_replace(parsed, table)
        elif alt == "IGNORE" or ignore:
            parsed.set("alternative", None)
            parsed.set("ignore", None)
            _do_nothing = sqlglot.parse_one(
                "INSERT INTO _ DEFAULT VALUES ON CONFLICT DO NOTHING", read="postgres"
            ).args.get("conflict")
            parsed.set("conflict", _do_nothing)
        # IDENTITY 表への INSERT は RETURNING <pk> を付与（lastrowid 用）
        if table in _IDENTITY_PK and parsed.args.get("returning") is None:
            pk = _IDENTITY_PK[table]
            parsed.set("returning", exp.Returning(expressions=[exp.column(pk)]))
            returning_pk = pk

    parsed = parsed.transform(_all_xform)
    out = parsed.sql(dialect="postgres")
    # psycopg3 paramstyle 対応: 文字列リテラル内の literal '%'（LIKE の '%.md' / 'enc:%' 等）は
    # '%%' へエスケープする（プレースホルダ '%s' は保護）。execute は常に params(tuple)を渡すため、
    # psycopg が '%%'→'%' に戻す。これをしないと literal '%' が不正プレースホルダ扱いで失敗する。
    out = out.replace("%s", "\x00PH\x00").replace("%", "%%").replace("\x00PH\x00", "%s")
    result = ("sql", out, returning_pk)
    with _XLATE_LOCK:
        _XLATE_CACHE[sql] = result
    return result


def _insert_table_name(parsed: "exp.Insert") -> str:
    t = parsed.this
    # INSERT INTO t (cols) は Schema(this=Table(...))、INSERT INTO t は Table
    if isinstance(t, exp.Schema):
        t = t.this
    if isinstance(t, exp.Table):
        return t.name
    return ""


def _rewrite_replace(parsed: "exp.Insert", table: str) -> "exp.Insert":
    """INSERT OR REPLACE → INSERT ... ON CONFLICT (<pk>) DO UPDATE SET <全列>=EXCLUDED。

    REPLACE の意味（衝突行を消して新規挿入＝省略列は既定値へ）を、全列 EXCLUDED 更新で等価再現する。
    pk が INSERT の列に含まれない（autoincrement 任せ）等で衝突対象を一意に決められない場合は
    OR REPLACE を外した素 INSERT にフォールバック（dead code 経路のみ該当）。
    """
    parsed.set("alternative", None)
    schema = parsed.this
    cols = []
    if isinstance(schema, exp.Schema):
        cols = [c.name for c in schema.expressions]
    pk_cols, all_cols = _table_meta(table)
    # 衝突キー＝pk（INSERT 列に全て含まれる場合のみ ON CONFLICT 化）
    if pk_cols and all(pc in cols for pc in pk_cols):
        set_exprs = []
        for c in all_cols:
            if c in pk_cols:
                continue
            set_exprs.append(
                exp.EQ(this=exp.column(c), expression=exp.column(c, table="excluded"))
            )
        # sqlglot のバージョン差異に頑健化：ON CONFLICT 片を直接組んで attach する。
        conflict_sql = "(" + ", ".join(pk_cols) + ") DO UPDATE SET " + ", ".join(
            f"{c} = EXCLUDED.{c}" for c in all_cols if c not in pk_cols
        )
        parsed.set("conflict", sqlglot.parse_one(f"INSERT INTO _ DEFAULT VALUES ON CONFLICT {conflict_sql}", read="postgres").args.get("conflict"))
        return parsed
    # フォールバック：OR REPLACE を外すだけ（衝突対象不明＝dead code 経路）
    logger.warning(f"[pg] INSERT OR REPLACE: 衝突キー不確定のため素 INSERT 化 table={table}")
    return parsed


# ─── テーブルメタ（pk 列・全列）catalog から取得・キャッシュ ───
_META_CACHE: dict[str, tuple] = {}
_META_LOCK = threading.Lock()


def _table_meta(table: str) -> tuple:
    """(pk_cols:list, all_cols:list) を information_schema/pg_catalog から取得（キャッシュ）。"""
    if table in _META_CACHE:
        return _META_CACHE[table]
    pk_cols: list = []
    all_cols: list = []
    try:
        # メインのコネクションプールから借りない（呼び出し側が接続保持中に入れ子取得すると
        # 並行時にプール枯渇/デッドロックする）。短命の直接接続でカタログを引く（取得結果はキャッシュ）。
        import psycopg

        with psycopg.connect(**_resolve_pg_params()) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (table,),
                )
                all_cols = [r[0] for r in cur.fetchall()]
                cur.execute(
                    "SELECT a.attname FROM pg_index i "
                    "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey) "
                    "WHERE i.indrelid = %s::regclass AND i.indisprimary",
                    (table,),
                )
                pk_cols = [r[0] for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[pg] table_meta 取得失敗 table={table}: {e}")
    meta = (pk_cols, all_cols)
    if all_cols:
        with _META_LOCK:
            _META_CACHE[table] = meta
    return meta


# ─── sqlite3.Row 互換の行（index と列名の両アクセス） ───
class _Row:
    __slots__ = ("_cols", "_vals", "_map")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals
        self._map = {c: i for i, c in enumerate(cols)}

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._vals[k]
        return self._vals[self._map[k]]

    def keys(self):
        return list(self._cols)

    def __len__(self):
        return len(self._vals)

    def __iter__(self):
        return iter(self._vals)

    def __contains__(self, k):
        return k in self._map

    def get(self, k, default=None):
        i = self._map.get(k)
        return self._vals[i] if i is not None else default


def _make_row_factory(cursor):
    cols = [d.name for d in (cursor.description or [])]

    def make(values):
        return _Row(cols, list(values))

    return make


# ─── cursor / connection ラッパ ───
# shim-fix-20260719 Part1: 読取判定と aborted-tx 判定のヘルパ。
_READ_ONLY_HEAD_RE = re.compile(r"^\s*(?:WITH\b.*?\)\s*)?SELECT\b", re.IGNORECASE | re.DOTALL)


def _is_read_only(tsql: str) -> bool:
    """翻訳後 SQL が読み取り専用（SELECT / WITH ... SELECT）かを判定する。
    書込・DDL の前では回復 rollback を一切行わないため、保守的に SELECT のみ True。"""
    if not tsql:
        return False
    head = tsql.lstrip()[:6].upper()
    if head.startswith("SELECT"):
        return True
    if head.startswith("WITH"):
        # CTE は末尾が INSERT/UPDATE/DELETE のこともあるため、書込語を含むなら読取扱いにしない。
        up = tsql.upper()
        return not any(w in up for w in (" INSERT ", " UPDATE ", " DELETE ", " MERGE "))
    return False


class _PgCursor:
    def __init__(self, raw_cursor):
        self._cur = raw_cursor
        self.lastrowid = None

    def _tx_aborted(self) -> bool:
        """接続の transaction が失敗中（INERROR）かを返す。autocommit や判定不能時は False。"""
        try:
            conn = self._cur.connection
            if getattr(conn, "autocommit", False):
                return False
            from psycopg.pq import TransactionStatus

            return conn.info.transaction_status == TransactionStatus.INERROR
        except Exception:
            return False

    def execute(self, sql, params=()):
        kind, tsql, ret_pk = _translate(sql)
        if kind == "pragma_read_journal":
            self._cur.execute("SELECT 'wal'")
            return self
        if kind == "pragma_read_fk":
            self._cur.execute("SELECT 1")
            return self
        if kind == "pragma_noop":
            self._cur.execute("SELECT 1 WHERE false")
            return self
        # shim-fix-20260719 Part1（後始末＝構造の欠陥への対処）:
        #   非 autocommit では、ある文が失敗すると transaction が aborted のまま残り、
        #   同じ接続を掴んだ後続文が InFailedSqlTransaction で連鎖失敗する（=接続汚染）。
        #   汚染を起こす失敗はアプリ側 try/except に握りつぶされ（PG 実行時経路で 51 読取/32 書込箇所）、
        #   握りつぶされたまま接続がハンドラ内で次の文へ回るため、無関係な読取が巻き込まれて 500 になる。
        #   ここでの後始末は「次に来るのが読取で、かつ既に txn が aborted の時だけ rollback して回復」に限定する。
        #   ・aborted な txn は、その中の未コミット書込がいずれ必ず失われる確定状態であり、
        #     いま rollback しても「成功していた書込を巻き戻す」ことにはならない（§1-1 / STOP-5 を回避）。
        #   ・書込文の前では一切 rollback しない＝書込経路の巻き戻し範囲に手を出さない。
        if _is_read_only(tsql) and self._tx_aborted():
            try:
                self._cur.connection.rollback()
                logger.warning(
                    "[pg] aborted-tx を検出。読取の前に rollback して接続を回復した"
                    "（前段で失敗が握りつぶされていた）。次文: %s",
                    tsql[:100],
                )
            except Exception as _e:  # pragma: no cover
                logger.warning("[pg] aborted-tx 回復の rollback に失敗: %s", _e)
        # 常に params(tuple) を渡す（空でも）。これにより psycopg が '%%'→'%' 復元を必ず行う。
        # 失敗そのものは中央で記録に残す（本ラン以前は握りつぶされ黙って消えていた＝根因）。再送はしない。
        try:
            self._cur.execute(tsql, tuple(params))
        except Exception as _e:
            logger.warning("[pg] 文の実行に失敗: %s: %s", _e.__class__.__name__, tsql[:160])
            raise
        if ret_pk is not None:
            try:
                row = self._cur.fetchone()
                if row is not None:
                    self.lastrowid = row[0]
            except Exception:
                pass
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size is not None else self._cur.fetchmany()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def __iter__(self):
        return iter(self._cur)

    def close(self):
        self._cur.close()


class _PgConn:
    """psycopg 接続を sqlite3.Connection 互換に見せるラッパ。close() でプールへ返却。"""

    def __init__(self, raw_conn, pool):
        self._conn = raw_conn
        self._pool = pool
        self._closed = False
        # sqlite3.Row 互換の行を返す row_factory を接続既定に。
        self._conn.row_factory = _make_row_factory

    # sqlite3 互換：row_factory 属性（set されても常に Row 互換を使う＝無害に受ける）
    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, _v):
        # 呼び出し側が sqlite3.Row を代入しても、互換 _Row を維持する
        self._conn.row_factory = _make_row_factory

    def execute(self, sql, params=()):
        cur = _PgCursor(self._conn.cursor())
        return cur.execute(sql, params)

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def executescript(self, script):
        # 複文を sqlglot で分割して逐次実行（SQLite 機構は Postgres で走らせない想定だが安全側）
        try:
            stmts = [s for s in sqlglot.parse(script, read="sqlite") if s is not None]
            for st in stmts:
                self.execute(st.sql(dialect="sqlite"))
        except Exception:
            for piece in script.split(";"):
                if piece.strip():
                    self.execute(piece)
        return self

    def commit(self):
        if not self._closed:
            self._conn.commit()

    def rollback(self):
        if not self._closed:
            self._conn.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        _broken = False
        try:
            self._conn.rollback()  # 未 commit を片付けてからプールへ返す
        except Exception:
            # DD-CYN-0116 F-3: rollback が通らない = 接続が既に死んでいる。
            # 従来は握り潰してプールへ返していたため、死んだ接続が次の借り手へ
            # 回っていた。返さずに捨てる (プールは足りない分を張り直す)。
            _broken = True
        if _broken:
            try:
                self._conn.close()
            except Exception:
                pass
            try:
                self._pool.putconn(self._conn)  # プールの貸出数を戻す (閉済は破棄される)
            except Exception:
                pass
            return
        try:
            self._pool.putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass

    # with conn: 互換（sqlite3 は成功時 commit・例外時 rollback）
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


class PostgresBackend(RelationalBackend):
    """関係層 Postgres バックエンド（db.py の差し替え口から使われる）。"""

    _pool = None
    _pool_lock = threading.Lock()

    @classmethod
    def _get_pool(cls):
        if cls._pool is not None:
            return cls._pool
        with cls._pool_lock:
            if cls._pool is None:
                import psycopg
                from psycopg_pool import ConnectionPool

                params = _resolve_pg_params()
                # プール上限は yaml database.postgres.pool_max（無ければ保守的既定 10）。新規 env 非使用。
                pool_max = 10
                try:
                    from core.config import CYNOVELA_CONFIG as _DTC

                    pm = ((_DTC.get("database") or {}).get("postgres") or {}).get("pool_max")
                    if pm:
                        pool_max = int(pm)
                except Exception:
                    pass
                conninfo = psycopg.conninfo.make_conninfo(**params)
                cls._pool = ConnectionPool(
                    conninfo=conninfo,
                    min_size=1,
                    max_size=pool_max,
                    max_idle=60.0,
                    # DD-CYN-0116 F-3: 貸し出す前に接続の生死を検査する。
                    # これが無いと Postgres 再起動直後、プールに残った死んだ接続を
                    # そのまま渡してしまい、最初に掴んだ処理だけが AdminShutdown で
                    # 落ちていた (以後は張り直されるので「1本だけ犠牲」に見えた)。
                    check=ConnectionPool.check_connection,
                    open=True,
                    name="cynovela-relational",
                )
                logger.info(f"[pg] 関係層プール初期化 max_size={pool_max} host={params.get('host')} db={params.get('dbname')}")
        return cls._pool

    def connect(self):
        pool = self._get_pool()
        raw = pool.getconn()
        try:
            raw.autocommit = False  # SQLiteBackend と同じ「明示 commit」作法に合わせる
        except Exception:
            pass
        return _PgConn(raw, pool)


def consolidated_ddl_path() -> str:
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "deploy", "postgres", "cynovela_pg_schema.sql")


def _split_pg_ddl_statements(script: str) -> list[str]:
    """Split Postgres DDL without breaking dollar-quoted blocks.

    The consolidated DDL contains `DO $$ ... $$;` blocks whose body has
    semicolons. A plain `script.split(";")` truncates those blocks and leaves a
    partially-created database. Keep this intentionally small and DDL-focused:
    comments are dropped, and semicolons split only outside quoted strings and
    dollar quotes.
    """
    out: list[str] = []
    buf: list[str] = []
    i = 0
    quote: str | None = None
    dollar: str | None = None
    while i < len(script):
        ch = script[i]
        nxt = script[i + 1] if i + 1 < len(script) else ""

        if quote is None and dollar is None and ch == "-" and nxt == "-":
            while i < len(script) and script[i] != "\n":
                i += 1
            continue

        if quote is None and ch == "$":
            j = i + 1
            while j < len(script) and (script[j].isalnum() or script[j] == "_"):
                j += 1
            if j < len(script) and script[j] == "$":
                tag = script[i : j + 1]
                if dollar is None:
                    dollar = tag
                elif dollar == tag:
                    dollar = None
                buf.append(tag)
                i = j + 1
                continue

        if dollar is None:
            if quote is None and ch in ("'", '"'):
                quote = ch
            elif quote == ch:
                if quote == "'" and nxt == "'":
                    buf.append(ch)
                    buf.append(nxt)
                    i += 2
                    continue
                quote = None

        if ch == ";" and quote is None and dollar is None:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
        else:
            buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def apply_consolidated_ddl(ddl_path: str | None = None) -> int:
    """Postgres に consolidated DDL を冪等適用する（schema 作成）。

    SQLite の migrate_db / numbered migrations は走らせない（方言非互換）。
    raw 接続で文単位に実行（CREATE ... IF NOT EXISTS のため再実行安全）。適用文数を返す。
    """
    import os

    path = ddl_path or consolidated_ddl_path()
    if not os.path.exists(path):
        logger.warning(f"[pg] consolidated DDL が見つかりません: {path}")
        return 0
    with open(path) as f:
        script = f.read()
    stmts = _split_pg_ddl_statements(script)
    pool = PostgresBackend._get_pool()
    n = 0
    with pool.connection() as c:
        with c.cursor() as cur:
            for s in stmts:
                cur.execute(s)
                n += 1
        c.commit()
    logger.info(f"[pg] consolidated DDL 適用 {n} 文")
    return n
