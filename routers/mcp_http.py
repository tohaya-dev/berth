"""DD-CYN-0132 Phase 6: HTTP で待ち受ける MCP エンドポイント (/api/mcp/rpc)。

背景 (現状の欠点):
  従来の MCP は mcp_server.py の 1 本で、標準入出力 (stdio) のみ・自前 JSON-RPC・
  MCP 層に認可なし (CYNOVELA_TOKEN を運搬するだけ)・版 2024-11-05。
  berth は複数レプリカ構成であり、stdio は待ち受け口が無く Service/LB の後ろに置けない。
  1 クライアント 1 プロセス専属で、berth の分散・フェイルオーバーを活かせない。

本エンドポイントの役割:
  - HTTP で待ち受ける (Service DNS の後ろで全レプリカが同じ口を出す。接続ごとの状態を持たない)。
  - 認可を MCP 層で持つ: 標準の _require_authenticated を通す。DD-CYN-0132 4-2 の
    用途限定の鍵 (cyn_...) も JWT もここで受理・検証される。資格なしは全拒否。
  - 版を現行 (2025-06-18) へ上げる。
  - 道具の返りを構造として返す (来歴 provenance を載せる)。
  - 鍵ごとに使える道具が変わる: 用途限定の鍵 (api_key) には外向き可の道具
    (検索・資料に基づく回答・読み取り情報) だけを見せ、範囲外の作業場所/コレクションへの
    到達を拒否する。管理系 (publish/create/audit) は admin の JWT でのみ見える。
  - 道具の一覧と説明文の指紋 (fingerprint) を持ち、変わったら気づけるようにする
    (GET /api/mcp/fingerprint)。

実装方針:
  道具の中身は各 API を再実装せず、同一 pod のループバック (127.0.0.1:PORT) 経由で
  本体 API に転送する。転送には呼び出し元の Bearer をそのまま使うため、伏字・役割・
  権限の判定は本体 API 側でそのまま効く。MCP 層は「認可の入口」「範囲の絞り込み」
  「版と構造化」を足すだけにする (単一の真実源を崩さない)。
"""

from __future__ import annotations

import hashlib
import json
import os

import requests
from urllib.parse import quote as _quote
from fastapi import APIRouter, HTTPException, Request

from core.auth import _require_authenticated

router = APIRouter(tags=["mcp"])

MCP_PROTOCOL_VERSION = "2026-07-28"
# DD-CYN-0143 M-1: 版を 2026-07-28 へ (server/discover = 状態を持たない発見)。
# initialize は廃止ではなく旧世代クライアント (goose 等 handshake 型) との互換のために残す:
# 本流 DD-CYN-0140 の実測で、現行 SDK も initialize で 2025-11-25 を頼んでくるため
# 版交渉は必須である。initialize が来ても server/discover と同じ内容で応えるだけで、
# 握手の完了 (notifications/initialized) は待たない (接続に状態を持たせない)。
# 交渉規則: 頼まれた版が日付形式で自分の版以下ならその版で応える。未来の版は名乗らない。
# Roots / Sampling / Logging は 2026-07-28 で非推奨のため実装しない。
import re as _re_mod

_MCP_REV_RE = _re_mod.compile(r"^\d{4}-\d{2}-\d{2}$")
MCP_SERVER_INFO = {"name": "cynovela-mcp", "version": "3.2"}


def _negotiate_version(requested: str) -> str:
    """版の交渉。日付形式で自分の版以下ならその版、それ以外は自分の版。"""
    if _MCP_REV_RE.match(requested or "") and requested <= MCP_PROTOCOL_VERSION:
        return requested
    return MCP_PROTOCOL_VERSION


# ─────────────────────────────────────────
# 道具の登録簿 (単一の真実源)
#   external=True : 用途限定の鍵 (api_key) にも見せてよい (検索・資料に基づく回答・読み取り情報)
#   external=False: 管理系。admin の JWT でのみ見せる
#   min_role      : 呼び出しに必要な最低役割
# ─────────────────────────────────────────
_TOOLS: list[dict] = [
    {
        "name": "search_collection",
        "external": True,
        "min_role": "viewer",
        "description": (
            "指定した1つのコレクションに RAG 検索を実行し、資料に基づく回答文と出典を返す。"
            "全資格で利用可 (viewer/APIキーには伏字後の内容で回答される)。"
            "返り: 回答テキスト・番号付きの出典一覧・来歴 (provenance: 伏字適用/役割/出典数)。"
            "回答本文の [N] は出典一覧の [N] に対応する。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace_id": {"type": "string"},
                "collection_id": {"type": "string"},
                "preset": {"type": "string", "description": "lite / standard / hq"},
            },
            "required": ["query", "workspace_id", "collection_id"],
        },
    },
    {
        "name": "search_across_collections",
        "external": True,
        "min_role": "viewer",
        "description": (
            "複数コレクションを横断して RAG 検索し、統合した回答と出典を返す。"
            "全資格で利用可 (viewer/APIキーには伏字後の内容で回答される)。"
            "返り: 回答テキスト・番号付きの出典一覧・来歴。回答本文の [N] は出典一覧の [N] に対応する。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "workspace_id": {"type": "string"},
                "collection_ids": {"type": "array", "items": {"type": "string"}},
                "preset": {"type": "string"},
            },
            "required": ["query", "workspace_id", "collection_ids"],
        },
    },
    {
        "name": "rag_general",
        "external": True,
        "min_role": "viewer",
        "description": (
            "資料 (索引) を使わず LLM の一般知識だけで回答する。出典は付かない。"
            "全資格で利用可。外部の LLM 宛のときは入力が伏字されてから送られる。返り: 回答テキスト "
            "(万一出典が付いた場合、回答本文の [N] は出典一覧の [N] に対応する)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "workspace_id": {"type": "string"}},
            "required": ["query", "workspace_id"],
        },
    },
    {
        "name": "list_workspaces",
        "external": True,
        "min_role": "viewer",
        "description": (
            "自分の資格で見える作業場所 (workspace) の一覧を返す。全資格で利用可。"
            "検索系ツールに渡す workspace_id はここで調べる。返り: workspace の id・名前の配列。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_collections",
        "external": True,
        "min_role": "viewer",
        "description": (
            "コレクション (検索の単位) の一覧を返す。workspace_id で絞り込み可。全資格で利用可。"
            "返り: コレクションの id・名前・状態 (ready なら検索可)・chunk 数の配列。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
        },
    },
    {
        "name": "get_workspace_info",
        "external": True,
        "min_role": "viewer",
        "description": "作業場所 (workspace) 1件の詳細を返す。全資格で利用可。返り: 名前・説明・作成日時など。",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "get_collection_info",
        "external": True,
        "min_role": "viewer",
        "description": (
            "コレクション1件の詳細を返す。全資格で利用可。"
            "返り: 名前・状態・chunk 数・egress_allowed (外へ出してよい印) など。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "collection_id": {"type": "string"},
            },
            "required": ["workspace_id", "collection_id"],
        },
    },
    {
        "name": "whoami",
        "external": True,
        "min_role": "viewer",
        "description": (
            "自分の資格を確かめる。全資格で利用可。接続の疎通確認にも使える。"
            "返り: 利用者名・役割 (admin/viewer)・APIキーなら scope (引ける範囲)。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_sources",
        "external": False,
        "min_role": "admin",
        "description": (
            "登録済みデータソース (取り込み元フォルダ) の一覧を返す (admin の JWT 専用)。"
            "返り: source の id・パス・状態・ファイル数の配列。scan_source に渡す source_id はここで調べる。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
        },
    },
    {
        "name": "list_ingest_roots",
        "external": False,
        "min_role": "admin",
        "tier": "read",
        "description": (
            "取り込み元 (ingest root) の一覧を返す (admin の JWT 専用)。"
            "返り: name・host_path・実在の有無 (exists) の配列と、追加可否の案内。"
            "source の追加は入口の ./launch.sh --add で行う (この道具は読み取りのみで、足す口は持たない)。"
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_audit_logs",
        "external": False,
        "min_role": "admin",
        "description": (
            "作業場所の直近の監査ログを返す (admin の JWT 専用)。"
            "返り: 時刻・利用者・操作・対象の配列 (最大 50 件)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "publish_collection",
        "external": False,
        "min_role": "admin",
        "tier": "write",
        "description": (
            "コレクションの Publish (取り込み・マスキング・索引化) を開始する (admin の JWT 専用)。"
            "非同期で即座に job_id が返る (DD-CYN-0143 M-3: 開始と進み具合を分ける。接続を保持しない)。"
            "進み具合は get_status に job_id を渡して見る。"
            "既に公開済み (ready) のコレクションへの再実行は索引を作り直す危険な操作のため "
            "confirm=true が要る。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "confirm": {"type": "boolean", "description": "再公開 (ready への再実行) のとき true 必須"},
            },
            "required": ["collection_id"],
        },
    },
    {
        "name": "create_workspace",
        "external": False,
        "min_role": "admin",
        "description": "作業場所 (workspace) を新規作成する (admin の JWT 専用)。返り: 作成された workspace の id と名前。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name"],
        },
    },
    # ---- DD-CYN-0136 2-2: 日常運用に要る4本を追加 ----
    {
        "name": "list_chunks",
        "external": True,
        "min_role": "viewer",
        "description": (
            "作業場所 (workspace) 内の索引済み chunk の一覧と本文を返す。出典の原文を確かめる用途。"
            "viewer と APIキーには伏字後 (masked) の本文だけが返り、admin の JWT には伏字前 (raw) が返る。"
            "filter で pii (PII を含む chunk のみ) / excluded (除外済みのみ) に絞れる。"
            "返り: 件数と chunk の配列 (chunk_id・出典ファイル名・本文)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "limit": {"type": "integer", "description": "最大件数 (既定 20)"},
                "offset": {"type": "integer", "description": "読み飛ばす件数 (既定 0)"},
                "filter": {"type": "string", "description": "all / pii / excluded (既定 all)"},
            },
            "required": ["workspace_id"],
        },
    },
    {
        "name": "create_collection",
        "external": False,
        "min_role": "admin",
        "description": (
            "コレクションを新規作成する (admin の JWT 専用)。作成直後は draft 状態で、"
            "link_files でファイルを結び、publish_collection で索引化すると検索可能になる。"
            "返り: 作成されたコレクションの id と名前。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "name": {"type": "string"},
                "access_level": {"type": "string", "description": "public / internal / confidential (既定 public)"},
            },
            "required": ["workspace_id", "name"],
        },
    },
    {
        "name": "link_files",
        "external": False,
        "min_role": "admin",
        "description": (
            "スキャン済みファイルをコレクションへ結びつける (admin の JWT 専用)。"
            "file_ids を指定すればその id 群を結ぶ。省略すると、まだどのコレクションにも"
            "結ばれていないファイル (unlinked-files) を全部結ぶ (取り込み漏れの一括回収)。"
            "返り: 結んだ件数。publish するまで索引には入らない。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "collection_id": {"type": "string"},
                "file_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["collection_id"],
        },
    },
    {
        "name": "scan_source",
        "external": False,
        "min_role": "admin",
        "tier": "write",
        "description": (
            "登録済みデータソース (取り込み元フォルダ) の再スキャンを開始する (admin の JWT 専用)。"
            "即座に返り、走査は裏で走る (DD-CYN-0143 M-3: 開始と進み具合を分ける)。"
            "進み具合は get_status に source_id を渡して見るのが第一 (終端 completed/failed/canceled)。"
            "一覧で見るなら get_status(section=sources) か list_sources の status (scanning→ready)。"
            "スキャン後は link_files → publish_collection の順で検索可能になる。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"source_id": {"type": "string"}},
            "required": ["source_id"],
        },
    },
    # ---- DD-CYN-0143 ステップ5 (P-3): 作業の単位で束ねた7本 ----
    {
        "name": "get_status",
        "external": True,
        "min_role": "viewer",
        "tier": "read",
        "description": (
            "稼働状態をまとめて返す。section で絞る: health (既定・詳細健康診断) / ready / "
            "queue (順番待ち列) / dashboard / stats (性能・モデル・RAG品質) / feedback / "
            "jobs (ジョブ一覧・admin。kind=all/publish/scan で絞れる) / "
            "sources (取り込み元と走査の進み具合・admin)。"
            "job_id を渡すとそのジョブ1件の進み具合を返す (公開の後追いに使う)。"
            "scan_source の返す source_id をここへ渡すと、その走査1件の進み具合 "
            "(status/file_count/last_scanned) と終端 (completed/failed/canceled) が見える。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "health / ready / queue / dashboard / stats / feedback / jobs / sources (既定 health)",
                },
                "job_id": {"type": "string", "description": "publish ジョブの id (指定時は section より優先)"},
                "source_id": {
                    "type": "string",
                    "description": "取り込み元の id (指定時は走査の進み具合1件を返す。section より優先)",
                },
                "kind": {"type": "string", "description": "section=jobs のとき: all / publish / scan (既定 all)"},
                "workspace_id": {"type": "string", "description": "section=sources のとき必須"},
            },
        },
    },
    {
        "name": "manage_settings",
        "external": False,
        "min_role": "admin",
        "tier": "admin",
        "description": (
            "Settings を扱う (admin の JWT 専用)。action=get で全設定 (export 封筒) を返す。"
            "action=set は settings を書き換える (環境変数 CYNOVELA_MCP_ALLOW_SETTINGS_WRITE=1 の"
            "ときだけ実行できる。既定は読みのみ)。restart_required の設定は反映に再起動が要る。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "get / set (既定 get)"},
                "settings": {"type": "object", "description": "set のとき: {key: value} の組"},
            },
        },
    },
    {
        "name": "manage_keys",
        "external": False,
        "min_role": "admin",
        "tier": "admin",
        "description": (
            "API キーを扱う (admin の JWT 専用)。action=list で一覧、action=issue で発行 "
            "(平文は1回だけ返る)、action=revoke で失効。revoke は危険な操作: 環境変数 "
            "CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1 と confirm=true の両方が要る。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list / issue / revoke (既定 list)"},
                "name": {"type": "string", "description": "issue のとき: 鍵の名前"},
                "key_id": {"type": "string", "description": "revoke のとき: 失効させる鍵の id"},
                "confirm": {"type": "boolean", "description": "revoke のとき true 必須"},
            },
        },
    },
    {
        "name": "manage_backups",
        "external": False,
        "min_role": "admin",
        "tier": "admin",
        "description": (
            "控え (バックアップ) を扱う (admin の JWT 専用)。action=list で一覧、action=create で"
            "作成 (時間がかかることがある)。action=restore は現行 DB を置き換える危険な操作: "
            "環境変数 CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1 と confirm=true の両方が要る。"
            "削除は MCP に載せない (CLI/GUI 限定)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list / create / restore (既定 list)"},
                "name": {"type": "string", "description": "restore のとき: 控えの名前"},
                "label": {"type": "string", "description": "create のとき: 控えに付ける印 (任意)"},
                "confirm": {"type": "boolean", "description": "restore のとき true 必須"},
            },
        },
    },
    {
        "name": "manage_archived",
        "external": False,
        "min_role": "admin",
        "tier": "admin",
        "description": (
            "片づけを扱う (admin の JWT 専用)。action=list で保管庫の一覧、action=restore で戻す。"
            "action=purge (完全削除)・vacuum (DB 圧縮)・orphans (孤児掃除) は危険な操作: "
            "環境変数 CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1 と confirm=true の両方が要る。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list / restore / purge / vacuum / orphans (既定 list)"},
                "kind": {"type": "string", "description": "restore/purge のとき: workspace / collection など"},
                "item_id": {"type": "string", "description": "restore/purge のとき: 対象の id"},
                "confirm": {"type": "boolean", "description": "purge/vacuum/orphans のとき true 必須"},
            },
        },
    },
    {
        "name": "manage_policies",
        "external": False,
        "min_role": "admin",
        "tier": "admin",
        "description": (
            "ガードレール/ポリシーを扱う (admin の JWT 専用)。action=list で一覧 (PII 検知の集計は "
            "action=pii)、action=create / update で作成・更新、action=delete で削除 "
            "(confirm=true 必須)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list / pii / create / update / delete (既定 list)"},
                "policy_id": {"type": "string", "description": "update/delete のとき: 対象の id"},
                "policy": {"type": "object", "description": "create/update のとき: ポリシーの中身"},
                "confirm": {"type": "boolean", "description": "delete のとき true 必須"},
            },
        },
    },
    {
        "name": "get_reports",
        "external": False,
        "min_role": "admin",
        "tier": "read",
        "description": (
            "報告書を扱う (admin の JWT 専用)。action=list で一覧、action=get で1件、"
            "action=generate で生成 (時間がかかる)。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "list / get / generate (既定 list)"},
                "report_id": {"type": "string", "description": "get のとき: 報告書の id"},
                "params": {"type": "object", "description": "generate のとき: 生成の指定 (任意)"},
            },
        },
    },
]

_ROLE_RANK = {"viewer": 1, "admin": 2}

# ─────────────────────────────────────────
# DD-CYN-0143 M-2: Resources / Prompts
#   リスク段位 (tier: read / write / admin) は資格 (min_role) とは別の軸として重ねる。
#   実体は本体 API への転送 (呼び出し元 Bearer をそのまま使う = 二重の守り)。
# ─────────────────────────────────────────
_RESOURCES: list[dict] = [
    {
        "uri": "cynovela://policies",
        "name": "保護ポリシー",
        "description": "ガードレール/マスキングのポリシー一覧 (いま効いている保護の設定)。",
        "mimeType": "application/json",
        "min_role": "viewer",
        "tier": "read",
        "path": "/api/policies",
    },
    {
        "uri": "cynovela://settings",
        "name": "Settings の値",
        "description": "settings 表の全キー (export 封筒つき)。管理の段位。",
        "mimeType": "application/json",
        "min_role": "admin",
        "tier": "admin",
        "path": "/api/settings/export",
    },
    {
        "uri": "cynovela://index-status",
        "name": "索引の状態",
        "description": "コレクション一覧と各索引の状態 (ready なら検索可・chunk 数)。",
        "mimeType": "application/json",
        "min_role": "viewer",
        "tier": "read",
        "path": "/api/collections",
    },
    {
        "uri": "cynovela://ingest-jobs",
        "name": "取り込みの進み具合",
        "description": "publish_jobs の直近一覧 (取り込み・公開ジョブの状態と進捗)。管理の段位。",
        "mimeType": "application/json",
        "min_role": "admin",
        "tier": "admin",
        "path": "/api/jobs?limit=20",
    },
    {
        "uri": "cynovela://ingest-roots",
        "name": "取り込み元の一覧",
        "description": "取り込み元 (ingest root) の一覧 (host_path と実在の有無)。追加は launch.sh --add。管理の段位。",
        "mimeType": "application/json",
        "min_role": "admin",
        "tier": "admin",
        "path": "/api/ingest-roots",
    },
]

_PROMPTS: list[dict] = [
    {
        "name": "diagnose_ingest_failure",
        "description": "取り込みが失敗した理由を診断する手順書。source_id か collection_id を渡す。",
        "arguments": [
            {"name": "source_id", "description": "取り込み元の id (list_sources で調べる)", "required": False},
            {"name": "collection_id", "description": "コレクションの id", "required": False},
        ],
    },
    {
        "name": "review_publish_readiness",
        "description": "このコレクションを公開してよいか点検する手順書。collection_id を渡す。",
        "arguments": [
            {"name": "collection_id", "description": "点検するコレクションの id", "required": True},
        ],
    },
]


def _prompt_messages(name: str, args: dict) -> list[dict]:
    """prompts/get の messages を組み立てる。"""
    if name == "diagnose_ingest_failure":
        target = []
        if args.get("source_id"):
            target.append(f"取り込み元 source_id={args['source_id']}")
        if args.get("collection_id"):
            target.append(f"コレクション collection_id={args['collection_id']}")
        target_s = "・".join(target) or "対象未指定 (まず list_sources / list_collections で特定する)"
        text = (
            f"Cynovela の取り込みが失敗した理由を診断してください。対象: {target_s}。\n"
            "手順:\n"
            "1. resource cynovela://ingest-jobs (または get_status の jobs) で失敗ジョブの error と stage を読む。\n"
            "2. list_sources で対象 source の状態とファイル数を確かめる。\n"
            "3. get_audit_logs で直近の関連操作を確かめる。\n"
            "4. 失敗の型 (パス不在 / 権限 / OOM / 形式非対応 / 中断) を特定し、根拠となる生の値を引用して報告する。\n"
            "推測で埋めず、取れた値だけで結論を出すこと。"
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]
    if name == "review_publish_readiness":
        cid = args.get("collection_id", "")
        text = (
            f"コレクション {cid} を公開してよいか点検してください。\n"
            "手順:\n"
            "1. get_collection_info で状態・chunk 数・egress_allowed を確かめる。\n"
            "2. list_chunks を filter=pii で叩き、PII を含む chunk の伏字が効いているか標本で確かめる。\n"
            "3. resource cynovela://policies でいま効いている保護ポリシーを確かめる。\n"
            "4. 「公開してよい / 直すべき点がある」を、根拠となる生の値を引用して判定する。\n"
            "閲覧者の資格で生の個人情報が見える状態なら、必ず「公開不可」と報告すること。"
        )
        return [{"role": "user", "content": {"type": "text", "text": text}}]
    raise KeyError(name)


def _loopback_base() -> str:
    """同一 pod の本体 API へ転送する先。CYNOVELA_PORT を最優先、無ければ 8765。"""
    port = os.environ.get("CYNOVELA_PORT") or "8765"
    return f"http://127.0.0.1:{port}"


def _canonical_fingerprint() -> str:
    """道具の一覧と説明文の指紋。名前・説明・スキーマを正規化して sha256 を取る。
    道具や説明文が変わると指紋が変わり、変化に気づけるようにする。"""
    canon = [
        {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
        for t in sorted(_TOOLS, key=lambda x: x["name"])
    ]
    blob = json.dumps(canon, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _tool_public(t: dict) -> dict:
    """MCP の tools/list へ出す形。役割を道具の仕様に載せる (annotation)。"""
    return {
        "name": t["name"],
        "description": t["description"],
        "inputSchema": t["inputSchema"],
        # 役割を最初から道具の仕様に載せる (6-2)。
        # DD-CYN-0143 M-2: リスク段位 (tier: read/write/admin) を資格と別軸で重ねる。
        # 定義に tier が無い既存道具は external=True→read / False→write とみなす。
        "annotations": {
            "cynovela_min_role": t["min_role"],
            "cynovela_external": t["external"],
            "cynovela_tier": t.get("tier") or ("read" if t["external"] else "write"),
        },
    }


def _visible_tools(role: str, is_api_key: bool, key_tools: list | None) -> list[dict]:
    """呼び出し元に見せてよい道具を絞る。
    - 役割が min_role 以上。
    - 用途限定の鍵 (api_key) には external=True の道具だけを見せる (外へ出すのは検索と
      資料に基づく回答まで)。
    - 鍵の scope.tools が指定されていれば、その集合にさらに絞る。
    """
    out = []
    for t in _TOOLS:
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(t["min_role"], 99):
            continue
        if is_api_key and not t["external"]:
            continue
        if is_api_key and key_tools and t["name"] not in key_tools:
            continue
        out.append(t)
    return out


def _scope_check(request: Request, args: dict) -> None:
    """用途限定の鍵の範囲を強制する。範囲外の作業場所/コレクションに到達させない。
    JWT (鍵でない) 呼び出しは本体 API 側の権限判定に委ね、ここでは絞らない。"""
    scope = None
    try:
        scope = getattr(request.state, "api_key_scope", None)
    except Exception:
        scope = None
    if not isinstance(scope, dict) or not scope:
        return  # 鍵でない or 範囲指定なし → 絞らない
    ws_allow = scope.get("workspaces") or []
    col_allow = scope.get("collections") or []
    if ws_allow:
        _ws = args.get("workspace_id")
        if _ws and _ws not in ws_allow:
            raise HTTPException(403, f"この鍵は作業場所 {_ws} の範囲外です")
    if col_allow:
        _cols = []
        if args.get("collection_id"):
            _cols.append(args["collection_id"])
        _cols.extend(args.get("collection_ids") or [])
        for _c in _cols:
            if _c not in col_allow:
                raise HTTPException(403, f"この鍵はコレクション {_c} の範囲外です")
        # agentK K-2 (q5 B2): search_across_collections に collection_ids: [] を渡すと
        # 本体 /api/chat が「WS 内全件」へ広げていた。名指しが空なら scope の集合を
        # 引数に書き戻し、転送先でも範囲内に閉じる (本体側 enforce_api_key_scope でも同じ判定)。
        if "collection_ids" in args and not (args.get("collection_ids") or []):
            args["collection_ids"] = [str(c) for c in col_allow if c]


def _forward(method: str, path: str, token: str, json_body=None, timeout: float = 120.0):
    """呼び出し元の Bearer をそのまま使い、同一 pod のループバック API へ転送する。
    伏字・役割・権限の判定は本体 API 側でそのまま効く (単一の真実源)。"""
    url = f"{_loopback_base()}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if method == "GET":
        return requests.get(url, headers=headers, timeout=timeout)
    return requests.post(url, headers=headers, json=(json_body or {}), timeout=timeout)


def _forward_delete(path: str, token: str, timeout: float = 30.0):
    """DELETE の転送 (DD-CYN-0143 ステップ5)。"""
    url = f"{_loopback_base()}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return requests.delete(url, headers=headers, timeout=timeout)


def _forward_put(path: str, token: str, json_body=None, timeout: float = 30.0):
    """PUT の転送 (DD-CYN-0143 ステップ5)。"""
    url = f"{_loopback_base()}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return requests.put(url, headers=headers, json=(json_body or {}), timeout=timeout)


def _admin_write_gate(args: dict, what: str) -> dict | None:
    """危険な操作の二重の門 (DD-CYN-0143 ステップ5 / 決定 §71-3 MRTR):
    環境変数 CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1 (既定で閉じる) と confirm=true の両方が要る。
    通れないときはエラーの返り (dict)、通れるときは None。"""
    if os.environ.get("CYNOVELA_MCP_ALLOW_ADMIN_WRITE", "").strip() != "1":
        return _structured(
            f"{what} は既定で閉じています (環境変数 CYNOVELA_MCP_ALLOW_ADMIN_WRITE=1 で開く)。",
            is_error=True,
        )
    if args.get("confirm") is not True:
        return _structured(f"{what} は危険な操作です。confirm=true を渡してください。", is_error=True)
    return None


def _structured(
    text: str,
    provenance: dict | None = None,
    is_error: bool = False,
    sources: list | None = None,
) -> dict:
    """道具の返りを構造として返す (来歴つき)。MCP の content は text だが、
    来歴は structuredContent にも載せて機械可読にする。
    N-8 (C-12 の MCP 側): 出典の構造体 ({index, file, ...} の配列) も
    structuredContent["sources"] に載せる。"""
    result = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if provenance is not None:
        result["structuredContent"] = {"provenance": provenance}
    if sources is not None:
        result.setdefault("structuredContent", {})["sources"] = sources
    return result


def _format_citation_lines(citations: list, sources: list) -> tuple[list[str], list[dict]]:
    """N-8 (C-12 の MCP 側): 出典を番号付きの行と構造体にする。
    citations (routers/chat.py の _build_cits 由来: index/source_filename/page_hint/score) を
    優先し、[index] を尊重する (本文の [N] と同じ番号空間。振り直さない・切り捨てない)。
    citations が空/無いときだけ sources (list[str]・順序保存済み) を enumerate で列挙する。"""
    lines: list[str] = []
    structs: list[dict] = []
    if citations:
        for c in citations:
            if not isinstance(c, dict):
                continue
            idx = c.get("index")
            fname = c.get("source_filename") or "不明"
            page = c.get("page_hint")
            line = f"[{idx}] {fname}"
            if page not in (None, ""):
                line += f" (p.{page})"
            lines.append(line)
            structs.append({"index": idx, "file": fname, "page_hint": page, "score": c.get("score")})
    else:
        for i, s in enumerate(sources or [], start=1):
            fname = (s.get("file_name") or str(s)) if isinstance(s, dict) else str(s)
            lines.append(f"[{i}] {fname}")
            structs.append({"index": i, "file": fname})
    return lines, structs


def _dispatch_tool(request: Request, user: dict, token: str, name: str, args: dict) -> dict:
    """MCP tools/call の 1 道具を実行する。範囲を検査し、本体 API へ転送する。"""
    _scope_check(request, args)
    role = user.get("role") or "viewer"

    # 役割の最低要件を MCP 層でも確認する (二重の守り)
    tdef = next((t for t in _TOOLS if t["name"] == name), None)
    if tdef is None:
        return _structured(f"未知の道具: {name}", is_error=True)
    if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(tdef["min_role"], 99):
        return _structured(f"道具 {name} には {tdef['min_role']} 以上の役割が必要です", is_error=True)
    is_api_key = bool(getattr(request.state, "api_key_id", None))
    if is_api_key and not tdef["external"]:
        return _structured(f"用途限定の鍵では道具 {name} は使えません", is_error=True)
    # agentK K-3 (q5 B3): 鍵の scope.tools は tools/list (_visible_tools) でしか効いておらず、
    # 一覧に出ない道具も tools/call で名指しすれば実行できた。呼び出し時にも同じ集合で絞る。
    if is_api_key:
        _kscope = getattr(request.state, "api_key_scope", None)
        _ktools = _kscope.get("tools") if isinstance(_kscope, dict) else None
        if _ktools and name not in _ktools:
            return _structured(f"この鍵では道具 {name} は使えません (scope.tools の範囲外)", is_error=True)

    try:
        if name in ("search_collection", "search_across_collections", "rag_general"):
            if name == "search_collection":
                body = {
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": [args["collection_id"]],
                    "preset": args.get("preset", "standard"),
                }
            elif name == "search_across_collections":
                body = {
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": args["collection_ids"],
                    "preset": args.get("preset", "standard"),
                }
            else:  # rag_general
                body = {
                    "query": args["query"],
                    "workspace_id": args["workspace_id"],
                    "collection_ids": [],
                    "rag_mode": "general",
                    "preset": "standard",
                }
            r = _forward("POST", "/api/chat", token, body, timeout=180)
            r.raise_for_status()
            d = r.json() if r.text else {}
            answer = d.get("answer", "")
            sources = d.get("sources", []) or []
            citations = d.get("citations", []) or []
            prov = d.get("provenance") or {}
            prov.setdefault("queried_as_role", role)
            # N-8 (C-12 の MCP 側): 出典を番号付きで全件列挙する (捨てない・切り捨てない)。
            # citations の [index] は本文の [N] と同じ番号空間 (C-12 で順序保証済み)。
            src_lines, src_structs = _format_citation_lines(citations, sources)
            text = f"## 回答\n{answer}\n\n## 出典 {len(src_lines)} 件"
            if src_lines:
                text += "\n" + "\n".join(src_lines)
            return _structured(text, provenance=prov, sources=src_structs)

        if name == "whoami":
            r = _forward("GET", "/api/auth/capabilities", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "list_workspaces":
            r = _forward("GET", "/api/workspaces", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "list_collections":
            qs = f"?workspace_id={args['workspace_id']}" if args.get("workspace_id") else ""
            r = _forward("GET", f"/api/collections{qs}", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "get_workspace_info":
            r = _forward("GET", f"/api/workspaces/{args['workspace_id']}", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "get_collection_info":
            if role == "admin":
                r = _forward("GET", f"/api/collections/{_quote(str(args['collection_id']), safe='')}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            # berth-ga-final-cleanup (q5 B12): GET /api/collections/{id} は admin 限定のため、viewer・鍵では
            # 所属・機密区分・allowed_roles で絞り込み済みの一覧から該当 1 件を返す (全資格で利用可の約束を守る)。
            r = _forward("GET", f"/api/collections?workspace_id={_quote(str(args['workspace_id']), safe='')}", token, timeout=15)
            r.raise_for_status()
            _hit = next((c for c in (r.json() or []) if str(c.get("id")) == str(args["collection_id"])), None)
            if _hit is None:
                return _structured(f"コレクション {args['collection_id']} は見つからないか、閲覧できません", is_error=True)
            return _structured(json.dumps(_hit, ensure_ascii=False, indent=2))

        if name == "list_sources":
            r = _forward("GET", f"/api/sources?workspace_id={args['workspace_id']}", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "list_ingest_roots":
            # N-7: 読み取りのみ。source の追加は launch.sh --add (MCP には足す口を持たせない)。
            r = _forward("GET", "/api/ingest-roots", token, timeout=15)
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "get_audit_logs":
            limit = min(int(args.get("limit", 10)), 50)
            r = _forward(
                "GET",
                f"/api/audit-logs?workspace_id={args['workspace_id']}&limit={limit}",
                token,
                timeout=15,
            )
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "publish_collection":
            # DD-CYN-0143 M-3: /publish/async で開始し job_id を即返す (接続を保持しない)。
            # 同期の /publish (timeout 600 と公開側 3600 の食い違い) は MCP から呼ばない。
            cid = args["collection_id"]
            rc = _forward("GET", f"/api/collections/{cid}", token, timeout=15)
            rc.raise_for_status()
            cstate = (rc.json() or {}).get("status") or (rc.json() or {}).get("state") or ""
            if str(cstate) in ("ready", "published") and args.get("confirm") is not True:
                return _structured(
                    f"コレクション {cid} は公開済み ({cstate}) です。再公開は索引を作り直す"
                    "危険な操作のため confirm=true を渡してください。",
                    is_error=True,
                )
            r = _forward("POST", f"/api/collections/{cid}/publish/async", token, {}, timeout=30)
            r.raise_for_status()
            d = r.json() if r.text else {}
            d.setdefault("progress_hint", "get_status に job_id を渡して進み具合を見る")
            return _structured(json.dumps(d, ensure_ascii=False))

        if name == "create_workspace":
            r = _forward(
                "POST",
                "/api/workspaces",
                token,
                {"name": args["name"], "description": args.get("description", "")},
                timeout=15,
            )
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False))

        # ---- DD-CYN-0136 2-2: 追加4本 ----
        if name == "list_chunks":
            limit = min(int(args.get("limit", 20)), 100)
            offset = max(int(args.get("offset", 0)), 0)
            flt = args.get("filter") or "all"
            r = _forward(
                "GET",
                f"/api/workspaces/{args['workspace_id']}/chunks?limit={limit}&offset={offset}&filter={flt}",
                token,
                timeout=30,
            )
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))

        if name == "create_collection":
            r = _forward(
                "POST",
                "/api/collections",
                token,
                {
                    "workspace_id": args["workspace_id"],
                    "name": args["name"],
                    "access_level": args.get("access_level", "public"),
                },
                timeout=30,
            )
            r.raise_for_status()
            return _structured(json.dumps(r.json(), ensure_ascii=False))

        if name == "link_files":
            cid = args["collection_id"]
            file_ids = args.get("file_ids") or []
            if not file_ids:
                # 省略時は未結線ファイルを全部結ぶ (取り込み漏れの一括回収)
                ru = _forward("GET", f"/api/collections/{cid}/unlinked-files", token, timeout=30)
                ru.raise_for_status()
                du = ru.json() if ru.text else {}
                file_ids = [f.get("id") for f in (du.get("files") or []) if f.get("id")]
                if not file_ids:
                    return _structured("結びつけるファイルがありません (unlinked-files は 0 件)")
            r = _forward(
                "POST",
                f"/api/collections/{cid}/link-files",
                token,
                {"file_ids": file_ids},
                timeout=60,
            )
            r.raise_for_status()
            d = r.json() if r.text else {}
            d["requested_file_ids"] = len(file_ids)
            return _structured(json.dumps(d, ensure_ascii=False))

        if name == "scan_source":
            # DD-CYN-0143 M-3: /scan/async で開始を即返す。進み具合は source の status で見る。
            r = _forward(
                "POST", f"/api/sources/{args['source_id']}/scan/async", token, {}, timeout=30
            )
            r.raise_for_status()
            return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))

        # ---- DD-CYN-0143 ステップ5 (P-3): 作業の単位で束ねた7本 ----
        if name == "get_status":
            if args.get("job_id"):
                r = _forward("GET", f"/api/jobs/{args['job_id']}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if args.get("source_id"):
                # N-9: 走査の進み具合1件 (status/file_count/last_scanned。終端 completed/failed/canceled)。
                r = _forward("GET", f"/api/sources/{args['source_id']}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            section = args.get("section") or "health"
            # N-9: section=jobs は kind (all/publish/scan) を透過する (既定 all)。
            kind = args.get("kind") or "all"
            # viewer には admin 専用の /health/detailed ではなく公開の /health を返す
            paths = {
                "health": ["/api/health/detailed" if role == "admin" else "/api/health"],
                "ready": ["/api/ready"],
                "queue": ["/api/queue/status"],
                "dashboard": ["/api/dashboard/summary"],
                "stats": ["/api/stats/performance", "/api/stats/model", "/api/stats/rag-quality"],
                "feedback": ["/api/feedback/stats"],
                "jobs": [f"/api/jobs?limit=10&kind={kind}"],
            }
            if section == "sources":
                _ws = args.get("workspace_id")
                if not _ws:
                    return _structured("section=sources には workspace_id が必要です", is_error=True)
                paths["sources"] = [f"/api/sources?workspace_id={_ws}"]
            if section not in paths:
                return _structured(f"未知の section: {section}", is_error=True)
            out = {}
            for p in paths[section]:
                r = _forward("GET", p, token, timeout=30)
                r.raise_for_status()
                out[p] = r.json() if r.text else {}
            return _structured(json.dumps(out, ensure_ascii=False, indent=2))

        if name == "manage_settings":
            action = args.get("action") or "get"
            if action == "get":
                r = _forward("GET", "/api/settings/export", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "set":
                if os.environ.get("CYNOVELA_MCP_ALLOW_SETTINGS_WRITE", "").strip() != "1":
                    return _structured(
                        "settings の書き込みは既定で閉じています "
                        "(環境変数 CYNOVELA_MCP_ALLOW_SETTINGS_WRITE=1 で開く)。",
                        is_error=True,
                    )
                body = args.get("settings")
                if not isinstance(body, dict) or not body:
                    return _structured("set には settings ({key: value}) が必要です", is_error=True)
                r = _forward("POST", "/api/settings/import", token, {"settings": body}, timeout=30)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        if name == "manage_keys":
            action = args.get("action") or "list"
            if action == "list":
                r = _forward("GET", "/api/keys", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "issue":
                if not args.get("name"):
                    return _structured("issue には name が必要です", is_error=True)
                r = _forward("POST", "/api/keys", token, {"name": args["name"]}, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False))
            if action == "revoke":
                gate = _admin_write_gate(args, "鍵の失効")
                if gate:
                    return gate
                if not args.get("key_id"):
                    return _structured("revoke には key_id が必要です", is_error=True)
                r = _forward_delete(f"/api/keys/{args['key_id']}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        if name == "manage_backups":
            action = args.get("action") or "list"
            if action == "list":
                r = _forward("GET", "/api/admin/backups", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "create":
                r = _forward("POST", "/api/admin/backup", token, {"label": args.get("label", "")}, timeout=300)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            if action == "restore":
                gate = _admin_write_gate(args, "控えの復元 (現行 DB を置き換える)")
                if gate:
                    return gate
                if not args.get("name"):
                    return _structured("restore には name が必要です", is_error=True)
                r = _forward("POST", f"/api/admin/backups/{args['name']}/restore", token, {}, timeout=300)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        if name == "manage_archived":
            action = args.get("action") or "list"
            if action == "list":
                r = _forward("GET", "/api/archived", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "restore":
                if not (args.get("kind") and args.get("item_id")):
                    return _structured("restore には kind と item_id が必要です", is_error=True)
                r = _forward("POST", f"/api/archived/{args['kind']}/{args['item_id']}/restore", token, {}, timeout=30)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            if action in ("purge", "vacuum", "orphans"):
                gate = _admin_write_gate(args, f"片づけの {action}")
                if gate:
                    return gate
                if action == "purge":
                    if not (args.get("kind") and args.get("item_id")):
                        return _structured("purge には kind と item_id が必要です", is_error=True)
                    r = _forward_delete(f"/api/archived/{args['kind']}/{args['item_id']}", token, timeout=60)
                elif action == "vacuum":
                    r = _forward("POST", "/api/admin/maintenance/vacuum", token, {}, timeout=300)
                else:
                    r = _forward("POST", "/api/admin/cleanup/chromadb-orphans", token, {}, timeout=300)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        if name == "manage_policies":
            action = args.get("action") or "list"
            if action == "list":
                r = _forward("GET", "/api/policies", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "pii":
                r = _forward("GET", "/api/pii-detections", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "create":
                if not isinstance(args.get("policy"), dict):
                    return _structured("create には policy (object) が必要です", is_error=True)
                r = _forward("POST", "/api/policies", token, args["policy"], timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False))
            if action == "update":
                if not (args.get("policy_id") and isinstance(args.get("policy"), dict)):
                    return _structured("update には policy_id と policy (object) が必要です", is_error=True)
                r = _forward_put(f"/api/policies/{args['policy_id']}", token, args["policy"], timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            if action == "delete":
                if args.get("confirm") is not True:
                    return _structured(
                        "ポリシーの削除は危険な操作です。confirm=true を渡してください。", is_error=True
                    )
                if not args.get("policy_id"):
                    return _structured("delete には policy_id が必要です", is_error=True)
                r = _forward_delete(f"/api/policies/{args['policy_id']}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        if name == "get_reports":
            action = args.get("action") or "list"
            if action == "list":
                r = _forward("GET", "/api/reports", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "get":
                if not args.get("report_id"):
                    return _structured("get には report_id が必要です", is_error=True)
                r = _forward("GET", f"/api/reports/{args['report_id']}", token, timeout=15)
                r.raise_for_status()
                return _structured(json.dumps(r.json(), ensure_ascii=False, indent=2))
            if action == "generate":
                r = _forward("POST", "/api/reports/generate", token, args.get("params") or {}, timeout=300)
                r.raise_for_status()
                return _structured(json.dumps(r.json() if r.text else {"ok": True}, ensure_ascii=False))
            return _structured(f"未知の action: {action}", is_error=True)

        return _structured(f"未知の道具: {name}", is_error=True)
    except HTTPException:
        raise
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        body = e.response.text[:200] if e.response is not None else ""
        return _structured(f"APIエラー ({code}): {body}", is_error=True)
    except Exception as e:
        return _structured(f"エラー: {e}", is_error=True)


@router.get("/api/mcp/fingerprint", response_model=None, summary="MCP ツール一覧の指紋 (変化検知用)")
def mcp_fingerprint(request: Request):
    """道具の一覧と説明文の指紋を返す。監視で差分を検出する用途 (変わったら気づける)。"""
    _require_authenticated(request)
    return {
        "fingerprint": _canonical_fingerprint(),
        "protocol_version": MCP_PROTOCOL_VERSION,
        "server_info": MCP_SERVER_INFO,
        "tool_count": len(_TOOLS),
        "tools": [t["name"] for t in sorted(_TOOLS, key=lambda x: x["name"])],
    }


@router.post("/api/mcp/rpc", response_model=None, summary="MCP JSON-RPC の口 (server/discover / tools/list / tools/call / ping)")
async def mcp_rpc(request: Request):
    """HTTP で待ち受ける MCP JSON-RPC の口 (接続ごとの状態を持たない streamable-http 風)。

    server/discover (2026-07-28) / initialize (旧世代互換) / tools/list / tools/call /
    ping に応える。認可は MCP 層でここで持つ:
    server/discover・initialize・ping 以外は _require_authenticated を通す (資格なしは全拒否)。
    """
    try:
        req = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}

    method = req.get("method", "")
    rid = req.get("id")
    is_notification = ("id" not in req) or (rid is None)

    if method in ("server/discover", "initialize"):
        # DD-CYN-0143 M-1: server/discover (2026-07-28) = 状態を持たない発見。
        # initialize は旧世代クライアント互換: 同じ内容で応えるだけで握手の完了は待たない。
        _req_ver = (req.get("params") or {}).get("protocolVersion") or ""
        _nego_ver = MCP_PROTOCOL_VERSION if method == "server/discover" else _negotiate_version(_req_ver)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "protocolVersion": _nego_ver,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": MCP_SERVER_INFO,
                "instructions": (
                    "Cynovela の RAG を MCP から使う口。認可は用途限定の鍵 (cyn_...) か JWT。"
                    "外へ出すのは検索と資料に基づく回答まで。返りには来歴 (provenance) が載る。"
                ),
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    # ここから先は認可が要る。資格なしは全拒否。
    try:
        user = _require_authenticated(request)
    except HTTPException as he:
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "error": {"code": -32001, "message": f"認可が必要です ({he.status_code})"},
        }

    role = user.get("role") or "viewer"
    is_api_key = bool(getattr(request.state, "api_key_id", None))
    key_scope = getattr(request.state, "api_key_scope", None) or {}
    key_tools = key_scope.get("tools") if isinstance(key_scope, dict) else None

    if method == "tools/list":
        vis = _visible_tools(role, is_api_key, key_tools)
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "tools": [_tool_public(t) for t in vis],
                "_meta": {"fingerprint": _canonical_fingerprint()},
            },
        }

    if method == "resources/list":
        # DD-CYN-0143 M-2: 役割で見えるものを絞る (読みの実行時は本体 API 側でも再判定される)。
        out = []
        for res in _RESOURCES:
            if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(res["min_role"], 99):
                continue
            out.append(
                {
                    "uri": res["uri"],
                    "name": res["name"],
                    "description": res["description"],
                    "mimeType": res["mimeType"],
                    "annotations": {"cynovela_min_role": res["min_role"], "cynovela_tier": res["tier"]},
                }
            )
        return {"jsonrpc": "2.0", "id": rid, "result": {"resources": out}}

    if method == "resources/read":
        uri = (req.get("params") or {}).get("uri") or ""
        res = next((x for x in _RESOURCES if x["uri"] == uri), None)
        if res is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"未知の resource: {uri}"}}
        if _ROLE_RANK.get(role, 0) < _ROLE_RANK.get(res["min_role"], 99):
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"resource {uri} には {res['min_role']} 以上の役割が必要です"}}
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        from fastapi.concurrency import run_in_threadpool

        def _read_resource():
            r = _forward("GET", res["path"], token, timeout=30)
            r.raise_for_status()
            return r.text

        try:
            body_text = await run_in_threadpool(_read_resource)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"resource の読みに失敗 ({code})"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": f"resource の読みに失敗: {e}"}}
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"contents": [{"uri": uri, "mimeType": res["mimeType"], "text": body_text}]},
        }

    if method == "prompts/list":
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "prompts": [
                    {"name": p["name"], "description": p["description"], "arguments": p["arguments"]}
                    for p in _PROMPTS
                ]
            },
        }

    if method == "prompts/get":
        params = req.get("params") or {}
        pname = params.get("name") or ""
        pargs = params.get("arguments") or {}
        pdef = next((p for p in _PROMPTS if p["name"] == pname), None)
        if pdef is None:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"未知の prompt: {pname}"}}
        for a in pdef["arguments"]:
            if a["required"] and not pargs.get(a["name"]):
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": f"引数 {a['name']} が必要です"}}
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"description": pdef["description"], "messages": _prompt_messages(pname, pargs)},
        }

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        targs = params.get("arguments", {}) or {}
        # 転送に使う Bearer は呼び出し元のものをそのまま使う (伏字・役割・権限を本体で効かせる)
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        # DD-CYN-0132 Phase6-fix: _dispatch_tool は本体 API へのブロッキング loopback
        #   (requests) を行う。この経路は同一プロセス (同一 pod) の 127.0.0.1:PORT を叩くため、
        #   async ハンドラのイベントループ上で直接ブロックすると自己呼び出しがデッドロックする
        #   (loopback 要求をループが処理できず Read timeout)。∴ threadpool へ逃がし、
        #   イベントループを解放して loopback 要求を並行に処理させる。
        from fastapi.concurrency import run_in_threadpool

        try:
            result = await run_in_threadpool(_dispatch_tool, request, user, token, name, targs)
        except HTTPException as he:
            result = _structured(f"拒否 ({he.status_code}): {he.detail}", is_error=True)
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}}
