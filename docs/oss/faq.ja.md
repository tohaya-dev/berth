[English](faq.md) | 日本語

# よくある質問

回答は、同梱のスクリプト、マニフェスト、コードで確かめられる範囲に限っています。まだ Berth をインストールしていない場合は[はじめに](start-here.ja.md)から読んでください。

## インストールと対応環境

### macOS や Windows ネイティブで動かせますか?

このリリースでは動かせません。提供しているのは Linux x86_64 上の K3s 構成です。具体的には、Windows 11 の WSL2 Ubuntu 24.04 ディストリビューション(動作確認済み)か、ネイティブの Ubuntu 24.04(アダプターはあるが実機での受け入れ試験は未了)です。K3s インストーラーは他のアーキテクチャでは停止し、systemd を必要とします。Windows ネイティブコンテナには対応していません。[既知の制限事項](limitations.ja.md#対応プラットフォーム)を参照してください。

### インストールにインターネット接続が必要なのはなぜですか?

インストーラーとイメージビルドが、次のものをネットワークから取得するためです。

- `get.k3s.io` の K3s インストーラーと、固定版の K3s リリース
- Hugging Face の埋め込みモデルとリランカーモデル(`tools/download-rag-models.py`)
- イメージビルドで使う `python:3.12-slim` ベースイメージ、Debian パッケージ、PyPI と PyTorch CPU 版配布元の Python パッケージ、spaCy モデルの wheel
- デプロイ時に K3s が取得する `pgvector/pgvector:pg16` と `redis:7.2-alpine` のイメージ

インストール後の Berth は、実行中にモデルをダウンロードしません(イメージで `HF_HUB_OFFLINE=1` を設定しています)。外部への通信は、設定した LLM プロバイダーと、有効にした任意の連携機能に向けたものだけです。

### ダウンロード量はどのくらいですか?

固定版モデルは合計 4,586,590,960 バイト(約 4.3 GiB)です。モデルごとのサイズは[モデルポリシー](model-policy.ja.md#サイズリビジョン保存場所)にあります。取り込んだアプリケーションイメージのサイズと、確保すべきディスク容量は[動作要件](requirements.ja.md)を参照してください。ソースツリー自体にモデルの重みは含まれていません。

### 既存の Kubernetes クラスターを使えますか?

自分で管理している本番以外のクラスターなら使えます。`install-k3s.sh` を飛ばし、自分の kubeconfig を `KUBECONFIG` に export して、その中のコンテキスト名を `BERTH_CONTEXT` に設定してください([Linux / WSL2 ガイド](linux-wsl.ja.md#新規インストール))。モデルと取り込み文書は `hostPath` ボリュームなので、対応するのはシングルノードだけです。

### GPU は使えますか?

Berth 本体では使えません。イメージには PyTorch の CPU 版が入っていて、マニフェストは GPU を要求しません。埋め込みモデルとリランカーは CPU で動きます。外部の LLM プロバイダーは、そのプロバイダー側の GPU を使えます。

## モデルと LLM

### 回答生成用の LLM は含まれていますか?

含まれていません。Berth がダウンロードするのは検索用の埋め込みモデルとリランカーモデルだけで、PII/NER 用のモデルはイメージの Python 依存関係に含まれています。インストール後、設定画面で外部プロバイダーを設定してください。設定するまでチャットはプロバイダーエラーを返しますが、取り込みと検索は動きます。

### どの LLM プロバイダーに接続できますか?

設定 API が受け付けるのは `lmstudio`(既定の種別)、`ollama`、`openai_compat`、`openrouter`、`vllm` です。`lmstudio` 以外は OpenAI 互換のアダプターを使います。エンドポイントは `http://` か `https://` で始まる必要があり、リンクローカルアドレスとクラウドのメタデータアドレスは拒否されます。エンドポイントへの接続は API Pod の中から行われるので、`localhost` や `127.0.0.1` はその Pod 自身を指します。Pod から届くアドレスを指定してください。

プロバイダーに接続すると、検索で取り出した文書テキストがそこへ送られます。リモートのプロバイダーなら、そのテキストはマシンの外へ出ます。

### モデルはどこに保存されますか?

`$BERTH_DATA_DIR/models`(例: `$HOME/.local/share/berth/models`)に、Hugging Face のキャッシュ形式(`models--BAAI--bge-m3/…`、`models--BAAI--bge-reranker-v2-m3/…`)で保存されます。API と worker の Pod はこれを `/app/store/models` に読み取り専用でマウントします。[モデルポリシー](model-policy.ja.md)を参照してください。

### モデルを別のものに入れ替えられますか?

このリリースは、2 つのモデルをリビジョンと SHA256 で `locks/models.json` に固定していて、受け入れ試験もそのモデルで行っています。他のモデルは試験していません。

## データ

### データはどこにありますか?

| データ | 場所 |
|---|---|
| データベース(ユーザー、設定、チャンク、ベクトル) | `$BERTH_DATA_DIR/volumes` 配下の PostgreSQL ボリューム(K3s の local-path ストレージ) |
| ジョブキュー | `$BERTH_DATA_DIR/volumes` 配下の Redis ボリューム |
| 元の文書 | `$BERTH_DATA_DIR/ingest/<フォルダー>`(Pod には読み取り専用でマウント) |
| モデル | `$BERTH_DATA_DIR/models` |
| 生成された秘密情報 | `$BERTH_DATA_DIR/secrets/`(`admin_password`、`pg_password`、`secret_key`) |
| レンダリング済みマニフェスト | `$BERTH_DATA_DIR/rendered/`(Secret を含む) |
| K3s の状態と kubeconfig | `$BERTH_DATA_DIR/k3s/`、`$BERTH_DATA_DIR/client.yaml` |
| バックアップ | `PG_BACKUP_DIR`(既定は `$HOME/dt-backups/k8s-pg`) |
| Windows ランチャーの状態 | `%LOCALAPPDATA%\Berth\<context>`(PID の記録と port-forward のログだけ) |

データベースには、文書の原文を `secret_key` で暗号化したものと、マスクした写しが並べて保存されます。

### サンプルデータはありますか?

サンプルの文書群はインストールされず、既存のデータベースも取り込まれません。ブートストラップ Job が新しい PostgreSQL データベースに管理者を作るだけです。リポジトリには小さなフィクスチャファイルが 4 つあります(`demo_data/d.txt`、`demo_data/p25/d.txt`、`ingest/test_pii_ingest.md`、`ingest/sub1/nested.txt`)。これらは `$BERTH_DATA_DIR/ingest` にコピーされません。PII 用のフィクスチャに入っているのは架空の値だけです。[配布・提供の前に](before-distributing.ja.md#同梱データ)を参照してください。

### どのファイル形式を取り込めますか?

`.txt`、`.md`、`.csv`、`.pdf`、`.docx`、`.pptx`、`.xlsx`、`.html`、`.htm`、`.eml`、`.zip` と、主な画像形式です。ただし制限があります(たとえば CSV は先頭 50 行だけ、スキャン PDF からは文字が取れない、など)。[形式の一覧](limitations.ja.md#取り込める形式と制限)を参照してください。

### バックアップはどう取りますか?

`PG_BACKUP_DIR` を設定して `./ops/linux.sh backup` を実行します。PostgreSQL のダンプ、チェックサムファイル、対になる暗号鍵のコピーが書き出され、ダンプは一時データベースで検証されます。復元するときは、API と worker を 0 台にスケールしてから `./ops/linux.sh restore DUMP --yes` を実行します([Linux / WSL2 ガイド](linux-wsl.ja.md#運用と復旧))。モデルと元の文書はバックアップに含まれないので、別に保管してください。

## アクセス

### 最初はどうやってサインインしますか?

`./ops/linux.sh connect`(または Windows ランチャー)を動かした状態で `http://127.0.0.1:18765` を開きます。ユーザー `cynovela` と、`$BERTH_DATA_DIR/secrets/admin_password` にあるパスワードでサインインしてください。

### 他のコンピューターからアクセスできますか?

既定ではできません。port-forward は `127.0.0.1` にバインドしています。`BERTH_ENTRY_ADDRESS` で公開範囲を広げられますが、port-forward は接続元を絞らないので、ファイアウォールでポートを制限してください。Kubernetes API のポート 26443 はすべてのインターフェースで待ち受けます。[既知の制限事項](limitations.ja.md#ネットワークへの公開)を参照してください。

### Berth を削除するには?

アンインストール用のコマンドはありません。[アンインストールとアップグレード](uninstall-upgrade.ja.md)を参照してください。
