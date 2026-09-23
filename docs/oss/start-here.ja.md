[English](start-here.md) | 日本語

# はじめに

Berth は、Ubuntu 24.04 上のシングルノード K3s で動く Kubernetes ワークロード一式(API Pod 2 つ、ワーカー Pod 2 つ、pgvector 付き PostgreSQL、Redis)です。Windows 11 では専用の WSL2 ディストリビューションの中で、またはネイティブの Ubuntu で動かします。このページは、何も入っていないマシンから最初の回答を得るまでの道筋です。以下のコマンドの正式な説明は [Linux / WSL2 ガイド](linux-wsl.ja.md) にあります。両者が食い違う場合はガイドに従ってください。

始める前に [動作要件](requirements.ja.md) を確認してください。x86_64、systemd、対話なしの `sudo`、メモリ約 16–24 GiB とディスク 40 GiB、初回ダウンロード用のインターネット接続が必要です。

## 全体の流れ

| # | 手順 | 何が起きるか | 所要時間(根拠) |
|---|---|---|---|
| 1 | Ubuntu の準備 | パッケージ、`PyYAML`・`cryptography`・`huggingface_hub` を入れた Python venv | 数分、回線次第(未計測) |
| 2 | 環境変数の設定 | `BERTH_*` 変数と `PYBIN` | — |
| 3 | `install-k3s.sh` | K3s `v1.35.5+k3s1` を systemd ユニット `k3s-<context>` として導入。データは `$BERTH_DATA_DIR/k3s`、API はポート 26443。`$BERTH_DATA_DIR/client.yaml` を書き出し、コンテキスト名を `$BERTH_CONTEXT` に変更 | ノードが Ready になるまで最大 300 秒待機(`install-k3s.sh`)。新規ホストでの実測なし |
| 4 | モデルのダウンロード | `tools/download-rag-models.py` が BAAI/bge-m3 と BAAI/bge-reranker-v2-m3 を固定リビジョンで `$BERTH_DATA_DIR/models` に取得し、全ファイルの SHA-256 を検証。4,586,590,960 バイト(約 4.3 GiB) | 記録済みの新規クローン検証で約 4.5 分、別の実行で約 6.5 分(大半はダウンロードとハッシュ検証)。Hugging Face の転送速度次第 |
| 5 | イメージのビルドと取り込み | Docker または Podman で `deploy/container/Containerfile` をビルドし、`docker save` / `podman save`、`sudo k3s ctr images import` | 通常もっとも時間がかかる手順(CPU 版 torch を含む pip install)。キャッシュなしのビルドは未計測。記録では `podman save` が 39 秒 |
| 6 | `ops/linux.sh install` | 下の「`install` が作るもの」を参照 | 稼働中のクラスターにイメージ取り込み済みの状態で、記録では 70 秒。API Pod は起動から約 50 秒で Ready。待機上限は PostgreSQL 300 秒、Redis 180 秒、bootstrap Job 300 秒、API 900 秒、ワーカー 600 秒 |
| 7 | `connect` または `serve` | port-forward `127.0.0.1:18765` → `service/cynovela-svc:8765` | すぐ |
| 8 | `verify` | レプリカ API 2/2、ワーカー 2/2、pgvector 1/1、Redis 1/1。`/api/health` が ok、`/api/ready`、`vector` 拡張の存在 | 数秒 |
| 9 | 初回ログインと LLM プロバイダー | `cynovela` でログインし、設定画面で外部の OpenAI 互換または Ollama プロバイダーを設定 | — |

実測値は、WSL2 検証ホストでの新規クローン検証 1 回(モデル、イメージ保存、install)と、同じホストの Pod タイムスタンプ(Ready まで約 50 秒)によるものです。目安であり、保証値ではありません。

## 1. Ubuntu の準備

Windows では、systemd を有効にした専用の Ubuntu 24.04 WSL2 ディストリビューションを用意します([linux-wsl.md](linux-wsl.ja.md)「Windows preparation」)。リポジトリは Linux ファイルシステム内(例: `~/Projects/berth`)にクローンし、以降はすべてそのルートで実行します。

```bash
sudo apt-get update
sudo apt-get install -y python3-venv curl git
python3 -m venv .venv
.venv/bin/pip install PyYAML cryptography huggingface_hub
```

## 2. 環境変数の設定

```bash
export PYBIN="$PWD/.venv/bin/python"
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export BERTH_ENTRY_PORT=18765
export BERTH_KUBE_PORT=26443
```

以降のコマンドは、新しいシェルでも毎回同じ値を必要とします。これらの行(と、後の手順で出てくる `KUBECONFIG` と `IMAGE`)をリポジトリの外のファイルに保存し、ターミナルごとに `source` してください。

**名前空間。** `ops/linux.sh` と Windows ランチャーの既定の名前空間は `berth` です。別の名前空間にインストールする場合は、どのシェルでもその値で `BERTH_NAMESPACE` を export してください。忘れたシェルは別の(たいてい空の)名前空間を見ることになり、`status` に何も表示されず、`connect` も失敗します。

`BERTH_CONTEXT` は `ops/linux.sh` のすべてのサブコマンドで必須で、K3s インストーラーでは `^berth-[a-z0-9-]+$` に一致する必要があります。`HAN_SOLO_*` の名前も非推奨の別名として引き続き受け付けますが、両方あれば `BERTH_*` が優先されます。

## 3. K3s のインストール

```bash
./deploy/k8s/linux/install-k3s.sh
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
```

インストーラーは既存の K3s があると中止します。本番以外の既存クラスターを使う場合はこの手順を飛ばし、自分が管理する kubeconfig を `KUBECONFIG` に、その中のコンテキスト名を `BERTH_CONTEXT` に設定します([linux-wsl.md](linux-wsl.ja.md))。cgroup v1(検証した WSL カーネル)では `failCgroupV1: false` を追加し、K3s ユニットに限って `GODEBUG=netdns=go` を設定します。

## 4. モデルのダウンロード

```bash
.venv/bin/python tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"
```

出力の最後に `BAAI/bge-m3 pinned files verified`、`BAAI/bge-reranker-v2-m3 pinned files verified`、`No generative model downloaded` が表示されれば成功です。サイズかハッシュが一致しないと `FAIL: model checksum mismatch` で止まります。Hugging Face に接続するのはこの手順だけで、Pod はこのディレクトリを読み取り専用でマウントし、`HF_HUB_OFFLINE=1` で動きます。回答生成用(LLM)のモデルはダウンロードしません。[モデルポリシー](model-policy.ja.md) を参照してください。

## 5. イメージのビルドと取り込み

公開イメージはないため、ローカルでビルドします。Docker の例です(Podman の場合は [linux-wsl.md](linux-wsl.ja.md) を参照)。

```bash
docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
docker save berth:rc-amd64 -o berth-amd64.tar
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth
export IMAGE=docker.io/library/berth:rc-amd64   # Podman の場合: localhost/berth:rc-amd64
```

`IMAGE` は `sudo k3s ctr images ls` が表示する参照名と完全に一致させてください。一致しないと Pod は `ImagePullBackOff` になります。取り込み後は `.tar` を削除してかまいません。

## 6. インストール

```bash
./ops/linux.sh install
```

### `install` が作るもの

| 順序 | オブジェクトまたはファイル | 補足 |
|---|---|---|
| 1 | `$BERTH_DATA_DIR/models`、`ingest`(モード 755)、`secrets` | なければ `render.py` が作成 |
| 2 | `$BERTH_DATA_DIR/secrets/pg_password`、`admin_password`、`secret_key` | 初回に一度だけ生成(モード 600)、以降は保持 |
| 3 | `$BERTH_DATA_DIR/rendered/infrastructure.yaml`、`bootstrap.yaml`、`workloads.yaml` | 描画済みマニフェスト |
| 4 | Namespace、Secret `cynovela-secret`、ConfigMap `cynovela-config` | ConfigMap には描画済みの `cynovela.yaml`(PostgreSQL、pgvector、Redis キュー、伏字並列度 1、到達できないループバックアドレスを指す LLM 設定)が入ります |
| 5 | `cynovela-pgvector`(PVC 2Gi)と `cynovela-redis`(PVC 1Gi) | ロールアウト完了を待機 |
| 6 | Job `hansolo-bootstrap` | スキーマと初期管理者を作成。`backoffLimit: 0` のため失敗しても再試行しません。ログを確認してください |
| 7 | Deployment `cynovela`(API、2)と `cynovela-worker`(2)、Service `cynovela-svc`(ClusterIP) | ロールアウト完了を待ち、`Runtime ready.` を表示 |

`install`(または `start`)を再実行すると描画と適用をやり直します。既存のシークレットとユーザーは保持されます。ボリュームは `$BERTH_DATA_DIR/volumes` 配下にあります。

## 7. 接続

```bash
./ops/linux.sh connect      # フォアグラウンドで動くので、このターミナルは開いたままにします
```

`connect` は接続先の Pod が入れ替わると終了します。`./ops/linux.sh serve` なら自動で再接続します。Windows では [ランチャー](windows-launcher.ja.md) が非表示の WSL クライアントで `serve` を動かし、ディストリビューションを起動したままにします。

## 8. 動作確認

同じ環境変数を設定した別のターミナルで実行します。

```bash
./ops/linux.sh verify
```

`replicas PASS`、`health PASS`、`pgvector PASS` と表示されれば正常です。

## 9. 初回ログインと最初の利用

1. `http://127.0.0.1:18765` を開きます。
2. `cynovela` でログインします。パスワードはインストール時に生成されています。`$BERTH_DATA_DIR/secrets/admin_password` からローカルで読み取ってください。ログ、チケット、コミットには貼り付けないでください。初回ログイン時のパスワード変更は強制されません。
3. UI は日本語で開くことがあります。言語は UI で切り替えられます。
4. 設定画面で外部の OpenAI 互換または Ollama プロバイダーを設定します。設定するまで、チャットはプロバイダーエラー(ガード済み)を返します。検索と取り込みはこの時点で動作します。
5. 文書を追加します。`$BERTH_DATA_DIR/ingest` 配下のフォルダーに置くか(Pod は uid 10001 で動くため、ディレクトリは `o+rx`、ファイルは `o+r`)、`./scripts/add-ingest-folder.sh` を使います([README](../../README.ja.md#取り込みフォルダーの追加))。その後「Add source」または Quick Start を使います。

## 次に読むもの

- [動作要件](requirements.ja.md)
- [Linux / WSL2 ガイド](linux-wsl.ja.md) — 詳しい説明、バックアップと復元
- [Windows ランチャー](windows-launcher.ja.md)
- [モデルポリシー](model-policy.ja.md)
- [トラブルシューティング](troubleshooting.ja.md)
- [アンインストールとアップグレード](uninstall-upgrade.ja.md)
- [制限事項](limitations.ja.md)
- [FAQ](faq.ja.md)
- [配布する前に](before-distributing.ja.md)
