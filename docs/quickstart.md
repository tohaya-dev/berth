# クイックスタート

Cynovela を初めて起動し、最初の RAG 質問を投げるまでの最短手順です。対象は `release/v1.0.0-alpha`（作業ディレクトリ `~/Projects/cynovela/cynovela`）です。

> さらに短い起動メモは [STARTUP.md](../STARTUP.md) を参照してください。

---

## 1. 前提環境

| 項目 | 内容 |
|---|---|
| Python | conda 環境 `cynovela`（Python 3.12 系で検証 / environment.yml は 3.12.13 を固定） |
| ローカル LLM | LM Studio もしくは OpenAI 互換 API（回答生成に必須） |
| 推奨 RAM | 起動モード（`--mode`）に応じて 4GB〜128GB |
| ネットワーク | 初回起動時のモデルダウンロードに必要 |

---

## 2. conda 環境のセットアップ

```bash
# conda 環境を作成（例: cynovela）
conda create -n cynovela python=3.12 -y

# 依存ライブラリをインストール
conda run -n cynovela python -m pip install -r requirements.txt
```

主な依存: FastAPI / uvicorn / ChromaDB / sentence-transformers / spaCy + ja-ginza / torch / pypdf ほか（`requirements.txt` 参照）。

---

## 3. SSL_CERT_FILE の注意（重要）

conda 環境では `SSL_CERT_FILE` が誤った証明書パスを指すことがあり、起動時の HuggingFace モデルダウンロードが失敗します。`unset` してシステムデフォルトの証明書を使ってください。

```bash
unset SSL_CERT_FILE
```

同梱の `launch.sh` はこの `unset` を内包しているため、これを使う場合は不要です。**手動で `conda run` を実行する場合のみ**、各自で実行してください。

---

## 4. 起動

### 方法 1: 同梱ランチャー（推奨）

```bash
cd <配布物を展開したフォルダ>

# launch.sh に渡した引数は、そのまま server.py へ届きます
# （実装: launch.sh の `exec "$PY" server.py "${APP_ARGS[@]}"`。2026-08-02 実測）。
# 引数なしは本番（空のデータベース）です。デモを見るなら --demo を明示します。
./launch.sh --demo            # デモデータ + 実 LLM（127.0.0.1 のみ）
./launch.sh --demo --lan      # デモデータ + LAN 公開
./launch.sh --check           # 起動せずに動く条件だけを調べる
```

停止:

```bash
./stop.sh
```

### 方法 2: 手動起動

```bash
cd ~/Projects/cynovela/cynovela
unset SSL_CERT_FILE

# デモデータ + 実 LLM（LM Studio を http://localhost:1234 で起動しておく）
conda run -n cynovela python server.py --demo

# LM Studio を使わない（下記の注意を参照）
conda run -n cynovela python server.py --demo
```

アクセス:

```bash
open http://127.0.0.1:8765
```

> ⚠️ **実 LLM が要ります**: 質問への答えを作るには LM Studio などの LLM が要ります。以前あった `--mock`（LLM を呼ばずに動かす指定）は撤去済みで、いま指定するとエラーで止まります。

---

## 5. 起動モード（`--mode`）と必要モデル

**`--mode` は、実際に読み込むモデルを切り替えません。** 変わるのは起動前のモデル存在確認（Preflight）とログの表示だけで、埋め込みは `cynovela.yaml` の既定（`BAAI/bge-m3`）で動きます。

| モード | Preflight が要求するモデル | 実際に読み込まれるモデル | 実サイズ |
|---|---|---|---|
| `full` | BAAI/bge-m3 + BAAI/bge-reranker-v2-m3 | BAAI/bge-m3 | 約 2.3GB（+ 再ランクで約 2.1GB） |
| `text`（既定） | BAAI/bge-m3 | BAAI/bge-m3 | 約 2.3GB（+ 再ランクで約 2.1GB） |
| `lite` | paraphrase-multilingual-MiniLM-L12-v2（約 470MB。読み込まれません） | BAAI/bge-m3 | 約 2.3GB |
| `lite-en` | paraphrase-MiniLM-L3-v2（約 22MB。読み込まれません） | BAAI/bge-m3 | 約 2.3GB |
| `minimal` | なし（Preflight スキップ） | BAAI/bge-m3 | 約 2.3GB |

> `lite` / `lite-en` / `minimal` の軽量化は**未配線**です。`--mode lite` を指定しても
> MiniLM は一度も読み込まれず、bge-m3 で動きます。`minimal` は Preflight を飛ばすため
> モデルが無くても起動しますが、最初の埋め込みで失敗します。
> **どのモードを選んでも、必要なモデルの大きさは変わりません。**

初回起動でモデルが未取得の場合、Preflight チェックの対話プロンプト（ダウンロード / 別モードへ切替 / キャンセル）が表示されます。非対話環境では `CYNOVELA_NONINTERACTIVE=1` を設定すると、未キャッシュ時に終了コード 2 で停止します。

```bash
# 例: lite を指定して起動（ただし読み込まれるのは bge-m3 です）
./launch.sh --demo --mode lite
```

---

## 6. デモアカウントでログイン

ブラウザで `http://127.0.0.1:8765` を開きます。`--demo` ではデモ用ユーザーが自動投入され、認証は `--demo` 起動でも強制されます（ログインで発行される JWT のみ受理し、固定トークンは受け付けません）。DB が保持するロールは **`admin` / `viewer` の 2 値**です。

検索そのものはロールによらず常に masked（伏字済み）層で行われます。違いは「当たった箇所の原文を金庫（関係DB の `tier='raw'` 行）から復号して見せるか」です。

| ロール | 権限 | 原文の提示 | デモ既定 WS |
|---|---|---|---|
| `admin` | 全機能 | あり（出口マスクなし） | `ws-tech` |
| `viewer` | 閲覧中心 | なし（出口マスクあり） | `ws-sales` |

> `curator` / `data-scientist` 等の名称は**受け付けません**。有効ロールは `admin` / `viewer` の 2 値だけで、DB の CHECK 制約と API の検査（HTTP 400）の両方で弾かれます。以前あった `viewer` への正規化表は撤去済みです。

---

## 7. 最初のファイル取り込みと Publish

1. ワークスペースを選択（デモでは `ws-tech` / `ws-sales` 等が投入済み）
2. 「コレクション作成」で名前と RAG 戦略を指定
3. ファイルをアップロード
4. 「Publish（公開）」を実行し `ready` 状態にする

Publish では テキスト抽出 → チャンク分割 → PII 検出/マスキング → Embedding 生成（ChromaDB 保存）→ BM25 インデックス構築 が行われます。進捗は SSE で返り、完了時に `publish_history` へ件数・所要時間が記録されます。

---

## 8. 最初の質問

`ready` 状態のコレクションに対し、RAG Chat 画面から質問します。

```
このドキュメントで扱われている主なトピックは何ですか？
```

回答には出典として `[1][2]` の引用番号付きでチャンクが表示されます。`admin` は raw 本文、`viewer` はマスク済み本文を検索し、`viewer` では LLM 出力にも出口マスクが適用されます。

---

## 9. 動作確認（テスト）

> **配布物には `tests/` は入っていません**（梱包時に外されます）。受け取った配布物では `pytest` / `make test` は実行できません。
> 動作を確かめるには `conda run -n cynovela python scripts/test_comprehensive_e2e.py` を使ってください。

```bash
# 開発ツリー（tests/ が在る側）での実行

# 手動 pytest（軽量・最初の失敗で停止）
cd ~/Projects/cynovela/cynovela
unset SSL_CERT_FILE
conda run -n cynovela python -m pytest -x -q
```

`Makefile` の `make test` / `make test-quick` / `make verify-live` も利用できます。`live` 系はサーバが `http://127.0.0.1:8765` で稼働していることが前提です。

---

## 次のステップ

- [architecture.md](architecture.md) — システム構成を理解する
- [handson-basic.md](handson-basic.md) — 基本操作を試す
- [rag-pipeline.md](rag-pipeline.md) — RAG パイプラインを理解する

---

## トラブルシューティング

- **モデルダウンロードや HTTPS が SSL で失敗** → `unset SSL_CERT_FILE` してから起動・テストしてください（ランチャー使用時は不要）。
- **`0.0.0.0` で起動できない** → LAN 公開には `--lan` が必要です。
- **品質が安定しない** → 実 LLM（LM Studio 等）に接続できているか確認してください。
- **admin パスワードを忘れた** → `conda run -n cynovela python server.py --reset-admin` で再発行できます。
- **ポート 8765 が使用中** → `lsof -i :8765` で確認し、`./stop.sh` または `pkill -f "python server.py"` で停止。

その他は [faq.md](faq.md) を参照してください。
