[English](README.md) | 日本語

# Berth

Berth は **Kubernetes ネイティブなセルフホスト型 RAG ランタイム**(手元の文書の取り込み・検索・回答生成)です。pgvector 付き PostgreSQL、Redis を使うワーカー、2 レプリカの API とワーカーの Deployment、バックアップと復元、外部 LLM プロバイダーの切り替え、CLI と HTTP MCP を備えます。ランタイムの前提は Linux 上の Kubernetes です。このリポジトリは Linux amd64 / Windows WSL2 の経路(Ubuntu 24.04 上の K3s)を **リリース候補**として提供するもので、新しい一般公開 GA リリースではありません。イメージを再配布する前に [第三者ライセンス通知](THIRD_PARTY_NOTICES.md) と [モデルポリシー](docs/oss/model-policy.ja.md) を読んでください。

Berth の内部のアプリケーションエンジンは *Cynovela* という名前です。この名前は Kubernetes のオブジェクト名(`cynovela`、`cynovela-svc` など)、設定ファイル `cynovela.yaml`、CLI `cynovela_cli.py`、初期管理者アカウント `cynovela` に現れます。別途インストールが必要な別製品ではありません。

> まずはここから: [はじめに](docs/oss/start-here.ja.md)(何もないマシンから最初の回答まで、所要時間つき)、続いて
> [Linux / WSL2 ガイド](docs/oss/linux-wsl.ja.md)(新規インストール、運用、復旧)と
> [Windows ランチャー](docs/oss/windows-launcher.ja.md)。

## このリポジトリが提供するもの

| 項目 | 値 |
|---|---|
| バージョン | `v1.0.0-ga`(`VERSION`。`/api/health` は `1.0.0-ga` を返します) |
| ランタイムの前提 | Kubernetes / Linux ワークロード |
| 検証済みの基盤 | Windows 11 WSL2 上の Ubuntu 24.04 amd64 で動く K3s(`deploy/k8s/linux/install-k3s.sh`) |
| ローカルの入口 | `./ops/linux.sh connect` または `serve` による `http://127.0.0.1:18765` |
| API の Deployment | `cynovela`、2 レプリカ |
| ワーカーの Deployment | `cynovela-worker`、2 レプリカ |
| データベース / ベクトル | PostgreSQL + pgvector |
| キュー / キャッシュ | Redis |
| アプリケーションイメージ | `deploy/container/Containerfile` からローカルでビルド(公開イメージはありません)。`IMAGE` には取り込んだ参照名を指定します |
| 初期管理者 | `cynovela`。パスワードはインストール時に `$BERTH_DATA_DIR/secrets/admin_password` に生成されます |

## 動作要件

概要: systemd が動く x86_64 の Ubuntu 24.04(Windows 11 上の専用 WSL2 ディストリビューション、またはネイティブ)、対話なしの `sudo`、イメージビルド用の Docker Engine または Podman、メモリ約 16–24 GiB(API とワーカーの Pod の要求は合計 5.5 GiB、上限まで使うと 24 GiB)、ディスク 40 GiB(固定モデルだけで約 4.3 GiB)。ポートは UI / API 用にループバックの 18765、Kubernetes API 用に 26443。インターネット接続は K3s、モデルのダウンロード、イメージビルドのために一度だけ必要で、実行時のモデル読み込みはオフラインです。詳細と根拠: [docs/oss/requirements.ja.md](docs/oss/requirements.ja.md)。

## インストール

リポジトリのルートで [docs/oss/start-here.ja.md](docs/oss/start-here.ja.md)(手順どおりに)または [docs/oss/linux-wsl.ja.md](docs/oss/linux-wsl.ja.md)(リファレンス)に従ってください。どのシェルでも同じ `BERTH_*` 変数を export してください。既定の `berth` 以外の名前空間にインストールする場合は `BERTH_NAMESPACE` も忘れないでください。概要: `./deploy/k8s/linux/install-k3s.sh` で固定バージョンの K3s を入れ(または `KUBECONFIG` / `BERTH_CONTEXT` を本番以外の既存クラスターに向け)、`tools/download-rag-models.py` で固定の埋め込み / リランカーモデルをダウンロードし、Docker か Podman でアプリケーションイメージをビルドして K3s に取り込んだら、次を実行します。

```bash
./ops/linux.sh install    # マニフェストを $BERTH_DATA_DIR/rendered に描画・適用し、ロールアウトを待つ
./ops/linux.sh connect    # http://127.0.0.1:18765 へのフォアグラウンド port-forward(動かしたままにする)
./ops/linux.sh verify     # 同じ環境変数を設定した別のターミナルで
```

`ops/linux.sh` のすべてのサブコマンドに `BERTH_CONTEXT` が必要で、`install` / `start` にはさらに `IMAGE` が必要です。`HAN_SOLO_*` の名前も `BERTH_*` 変数の非推奨の別名として引き続き受け付けます(両方あれば `BERTH_*` が優先)。生成されたパスワードで `cynovela` としてログインし、設定画面で外部の OpenAI 互換または Ollama プロバイダーを設定してください。新規インストール直後は回答生成の接続先に到達できないため、設定するまで RAG チャットはガード済みのプロバイダーエラーを返します(検索と取り込みは設定なしで動きます)。

## 運用

```bash
./ops/linux.sh status     # 名前空間の Pod、Deployment、Service、PVC
./ops/linux.sh verify     # API 2/2 とワーカー 2/2 が Ready、/api/health が ok、/api/ready、pgvector 拡張の存在
./ops/linux.sh restart    # API とワーカーのローリング再起動
./ops/linux.sh stop       # API、ワーカー、Redis、PostgreSQL を 0 にスケール
./ops/linux.sh start      # install と同じ処理: 描画し直し、適用し直し、ロールアウトを待つ(IMAGE が必要)
./ops/linux.sh serve      # Pod の入れ替え後に再接続する port-forward(Windows ランチャーが使用)
```

`connect` は動かし続ける必要があり、接続先の Pod が終了したら起動し直してください(または `serve` を使います)。Windows で常駐させるには [`ops/windows-wsl.ps1`](docs/oss/windows-launcher.ja.md) を使います。

## 取り込みフォルダーの追加

Linux / WSL2 の経路では、取り込み領域はホストのディレクトリ(`$BERTH_DATA_DIR/ingest`。Pod には `/app/ingest` として読み取り専用でマウント)なので、取り込みフォルダーの追加はホストにフォルダーを作るだけです。再起動、ロールアウト、PVC や Deployment の変更は不要です。Pod は非 root の uid `10001` で動くため、フォルダーはたどれる権限、ファイルは全員が読める権限が必要です(ディレクトリ `o+rx`、ファイル `o+r`)。ヘルパーはその権限でフォルダーを作成し、必要なら文書をコピーして(`o+r` にして)、ソースとして登録し、スキャンします。フォルダーは「Add source」と Quick Start のフォルダー選択にも表示されます。

```bash
./scripts/add-ingest-folder.sh contracts --from ~/Documents/contracts   # フォルダー作成だけなら --no-register を付ける
```

手作業でフォルダーを作る場合は `chmod o+rx "$BERTH_DATA_DIR/ingest/<folder>"` とファイルへの `chmod o+r` を実行してください。ヘルパーがソースを登録するには `./ops/linux.sh connect`(または `serve`)が動いている必要があります。

## バックアップ / 復元

`ops/linux.sh backup|restore` は、設定したコンテキスト、名前空間、シークレットキーを使って `deploy/k8s/pg-backup.sh` を呼び出します。

```bash
export PG_BACKUP_DIR="$PWD/_oss-rc-backups"
./ops/linux.sh backup
./ops/linux.sh restore "$PG_BACKUP_DIR/pgdump-TIMESTAMP.dump" --yes   # 先に API とワーカーを 0 にスケール。ガイド参照
```

`backup` はダンプ、チェックサムの付随ファイル、対になる `secret.key-TIMESTAMP` を書き出します。`restore` は稼働中のデータベースに触れる前に、一時データベースでダンプを検証し、復元先のキーを確認します。復元を確認済みのバックアップを少なくとも 1 つ、キーと一緒に保管してください。モデルと取り込み元のファイルは別途保管が必要です。

## モデル

`tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` は、Berth の検索に必要な固定の埋め込みモデルとリランカーモデル(`locks/models.json`)だけをダウンロードします。PII / NER モデルはイメージの Python 依存関係に含まれます。バージョンとライセンスは [モデルポリシー](docs/oss/model-policy.ja.md) を参照してください。

**回答生成用の LLM モデルファイルは同梱もダウンロードもしません。** Berth は、利用者が設定する外部の LM Studio、Ollama、またはその他の OpenAI 互換プロバイダーに接続します。

## ソーススナップショット

このリポジトリはランタイムのソーススナップショットです。`manifest.json` には、同梱ファイルごとの SHA-256、実行可能ファイル(`modes`)、`product` 名、スナップショットの作成元である開発ツリーのコミット `commit` が記録されています。ビルドと受け入れ試験を行ったのは Linux amd64 だけです。[Linux / WSL2 ガイド](docs/oss/linux-wsl.ja.md) のプラットフォーム表を参照してください。

## CLI / MCP

`cynovela_cli.py --help` で CLI のサブコマンドを一覧できます。HTTP MCP エンドポイントは API が提供します
(ツール一覧と、強制される API キーのスコープは `routers/mcp_http.py` を参照)。

Berth での CLI の推奨設定:

```bash
export CYNOVELA_URL=http://127.0.0.1:18765
python3 cynovela_cli.py status
python3 cynovela_cli.py login --username cynovela
```

HTTP MCP:

```text
POST /api/mcp/rpc
GET  /api/mcp/fingerprint
```

連携には、長期間有効な管理者の認証情報ではなく、権限を最小限に絞ったスコープ付き API キーで HTTP MCP を使うことを推奨します。

## 既知の制限

- シングルノードのみ: モデルキャッシュと取り込み領域は K3s ノード上の hostPath ボリュームです。マルチノードクラスターは未検証です。
- 新規インストール直後は回答生成プロバイダーがありません。チャットの回答を得るには外部 LLM を設定してください。
- ネイティブ Ubuntu 24.04 実機での受け入れ試験と、Windows ホスト再起動の試験は未実施です(ガイドとランチャーの文書を参照)。
- ソースコードには、Cynovela と共有してきた経緯から受け継いだスタンドアロン互換の経路が残っています。これらは現在の Berth のアーキテクチャではありません。
- ほかのアーキテクチャは、アプリケーションイメージとネイティブ依存関係をその上でビルドし受け入れ試験をするまで、PASS とはみなしません。

全体は [docs/oss/limitations.ja.md](docs/oss/limitations.ja.md) を参照してください。

## ドキュメント

- [`docs/oss/start-here.ja.md`](docs/oss/start-here.ja.md) — はじめに: インストールの流れ、初回ログイン、各手順で起きること
- [`docs/oss/requirements.ja.md`](docs/oss/requirements.ja.md) — 動作要件: OS、ポート、CPU / メモリ、ディスク、ネットワーク
- [`docs/oss/linux-wsl.md`](docs/oss/linux-wsl.ja.md) — Linux amd64 / Windows WSL2: インストール、運用、復旧(英語)
- [`docs/oss/windows-launcher.md`](docs/oss/windows-launcher.ja.md) — WSL2 ランタイム用の Windows ランチャー(英語)
- [`docs/oss/troubleshooting.ja.md`](docs/oss/troubleshooting.ja.md) — 症状、原因、対処
- [`docs/oss/uninstall-upgrade.ja.md`](docs/oss/uninstall-upgrade.ja.md) — アンインストール、アップグレード、再インストール
- [`docs/oss/limitations.ja.md`](docs/oss/limitations.ja.md) — 既知の制限
- [`docs/oss/faq.ja.md`](docs/oss/faq.ja.md) — よくある質問
- [`docs/oss/before-distributing.ja.md`](docs/oss/before-distributing.ja.md) — イメージや環境を配布する前に読むこと
- [`docs/oss/model-policy.ja.md`](docs/oss/model-policy.ja.md) — 埋め込み / リランカーモデルのポリシー
- [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) と [`docs/oss/dependency-licenses.csv`](docs/oss/dependency-licenses.csv) — 第三者ライセンス
- [`SECURITY.ja.md`](SECURITY.ja.md) — セキュリティ
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)

## ライセンス

MIT
