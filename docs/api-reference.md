> **このドキュメントについて**
> Cynovelaは、AI基盤ツールのコンセプトを個人が手を動かして理解するために作った
> 完全非公式の学習ツールです。商用製品・公式実装ではありません。
> 実装はすべてオリジナルで、FastAPI / SQLite / ChromaDB / BGE-M3 / ローカルLLM
> という OSS スタックで構成されています。
> 会社・製品の公式見解を一切代表しません。

# API リファレンス（概要）

Cynovela は FastAPI で構築されており、`routers/` 配下の **36 ファイル** にエンドポイントが分かれて配置されています。本ドキュメントは個別のエンドポイント仕様ではなく、**全体マップとして主要カテゴリを一望** できるようにまとめたものです。

---

## 1. 認証方式

### 1.1 認証ヘッダー

API リクエストは原則として `Authorization` ヘッダーで認証情報を渡します。

- 通常モードでは `username` と `password` によるログインを経て、サーバー側で発行されたトークンを利用します。
- 認証は `POST /api/auth/login` が発行する JWT のみです。かつて存在した `Bearer demo-token-{user_id}` 形式の簡易トークンは 2026-07-29 に廃止され、`--demo` 起動でも受理しません。
<!-- BACKLOG: JWT 化後のトークン形式は未定 -->

### 1.2 認可（権限チェック）

各エンドポイントの先頭で、`core/auth.py` のロール検査ヘルパーを呼び出します。

| ヘルパー | 検査内容 |
|---|---|
| `_require_admin()` | role == admin |
| `_require_authenticated()` | 認証済み（ロール不問） |
| `_require_role(roles)` | 指定ロールのいずれかに合致 |
| `_require_admin_or_self()` | admin または本人 |

ロール検査の呼び出しは **約 242 箇所** に分散しています。
詳細は `docs/rbac.md` を参照してください。

---

## 2. 主要エンドポイントのカテゴリ別一覧

### 2.1 認証・ユーザー管理

| ルーター | 主な役割 |
|---|---|
| `auth.py` | ログイン、ログアウト、ユーザー作成・削除・一覧（admin 限定） |
| `users.py` | ユーザー詳細、自分自身の情報 |
| `sessions.py` | チャットセッションの一覧・削除（一部 admin 限定） |

### 2.2 データソース・ファイル管理

| ルーター | 主な役割 |
|---|---|
| `sources.py` | 取り込み元（Source）の登録・スキャン |
| `files.py` | ファイルのアップロード・一覧・削除（admin 限定の操作あり） |
| `archived.py` | アーカイブの照会・復元（admin 限定） |

### 2.3 ワークスペース・コレクション

| ルーター | 主な役割 |
|---|---|
| `workspaces.py` | ワークスペースの作成・削除・詳細 |
| `collections.py` | コレクションの作成・編集・Publish |
| `catalog.py` | データカタログの参照 |

### 2.4 RAG / Chat

| ルーター | 主な役割 |
|---|---|
| `chat.py` | チャット応答（通常 / 比較 / SSE ストリーム） |
| `agent.py` | エージェント系の操作 |
| `mcp.py` | MCP（Model Context Protocol、外部公開ツール）の管理 |
| `messages.py` | メッセージ単位の操作 |

### 2.5 ガードレール・コンプライアンス

| ルーター | 主な役割 |
|---|---|
| `guardrails.py` | PII 検出履歴の集計（admin 限定）、禁止トピックの CRUD |
| `policies.py` | ガードレールポリシーの一覧・編集 |
| `compliance.py` | コンプライアンス系の操作（admin 限定） |
| `feedback.py` | 👍👎 フィードバックの取得・編集（admin 限定） |

### 2.6 モデル・LLM 接続

| ルーター | 主な役割 |
|---|---|
| `llm.py` | LLM プロバイダー設定（admin 限定） |
| `lmstudio.py` | LM Studio 固有の操作 |
| `models.py` | モデル設定（admin 限定） |
| `mode.py` | 起動モード（full / text / lite / lite-en / minimal）参照 |
| `pipeline_config.py` | RAG プリセットの取得 |

### 2.7 監視・運用

| ルーター | 主な役割 |
|---|---|
| `dashboard.py` | ダッシュボード用のサマリー |
| `health.py` | 健全性確認（一部 admin 限定） |
| `stats.py` | 統計情報 |
| `alerts.py` | アラート（admin 限定） |
| `audit_logs.py` | 監査ログ閲覧 |
| `jobs.py` | バックグラウンドジョブの参照 |
| `cost.py` | コスト集計 |
| `reports.py` | レポート系 |

### 2.8 設定・その他

| ルーター | 主な役割 |
|---|---|
| `settings.py` | 各種設定の参照・更新（PII 検出モード切替の `/api/settings/pii-mode` を含む） |
| `features.py` | 機能トグルの参照 |
| `admin.py` | 管理者向け操作 |
| `demo.py` | デモデータ管理 |
| `pages.py` | 静的ページ |

---

## 3. リクエスト / レスポンスの基本形式

### 3.1 共通ヘッダー

- `Content-Type: application/json`
- `Authorization: Bearer <token>`
- `X-Request-ID: <uuid>` … サーバー側で全リクエストに付与（`logging.request_id = true` の既定）

### 3.2 レスポンス

JSON 形式が基本です。エラー時は次の形式で返します。

```json
{
  "error": {
    "code": "BAD_REQUEST",
    "message": "action must be 'block' or 'warn'"
  }
}
```

主なエラーコード例:

| コード | HTTP | 用途 |
|---|---|---|
| `BAD_REQUEST` | 400 | 入力検証エラー |
| `INVALID_REGEX` | 400 | 正規表現のコンパイル失敗 |
| 認可エラー | 403 | ロール不足 |
| 410 Gone | 410 | 廃止されたエンドポイント（例: `/chat-popup`） |

### 3.3 SSE（Server-Sent Events）

チャットのストリーミング応答は SSE 形式で返します。

- イベント単位で `event:` と `data:` を改行区切りで送出
- 接続が SSE スレッドから DB を参照する場合も `db.get_db(check_same_thread=False)` で安全に扱える設計

---

## 4. レート制限

- 既定で SlowAPI を用いたレートリミットが有効です。
- 環境変数 `CYNOVELA_DISABLE_RATE_LIMIT` を `1` / `true` / `yes` のいずれかに設定すると無効化できます（テスト時の利用を想定）。

---

## 5. アップロード上限

- ファイルアップロードの既定上限は **100 MB** です。
- 環境変数 `CYNOVELA_MAX_UPLOAD_BYTES` で変更できます。

---

## 6. ページネーション

主要な一覧 API（list 系エンドポイント）はページネーションに対応しています。

| パラメータ | 用途 |
|---|---|
| `limit` | 1 ページの件数（既定はエンドポイントごと） |
| `offset` | 取得開始位置 |

監査ログ取得（MCP の `get_audit_logs`）の `limit` は既定 10 / 最大 50 です。

---

## 7. 既知制限

- API 経由での **監査ログの削除・改ざんは禁止** されています（追加のみ）。
- 認証強制は起動形態によらず動作します。`--demo` 起動でも認証チェックは省かれません（`Bearer demo-token-<user_id>` 形式の固定トークンは 2026-07-29 に廃止し、一律 401 で拒否します）。
- `/api/sources` の path パラメータは、システムパスへのアクセスを防ぐためのバリデーションが必要です。
- `llm_endpoint` は内部ネットワークを指す値に変更できないようにバリデーションされます。
- LLM プロンプトを構成する際、システムプロンプトは retrieved_content の **後** に配置する設計です（前置きするとドキュメントで上書き可能になるため）。
- 一部のエンドポイントは将来実装予定の機能（Qdrant、MLX、GraphRAG、エージェント実行基盤など）に依存しており、現状は骨格のみです。
<!-- BACKLOG: 各エンドポイントの個別仕様（パス、メソッド、入出力スキーマ）の網羅はまだ整理途中 -->

---

最終更新: 2026-05-26 / Alpha GA 対応版
