[English](linux-wsl.md) | 日本語

# Linux amd64 / Windows WSL2 リファレンス

このリリースでは、PostgreSQL/pgvector + Redis + API 2 台 + ワーカー 2 台の構成に、K3s を直接使うアダプタ（`ops/linux.sh`、`deploy/k8s/linux/`）を追加しています。Podman と Docker はどちらもイメージのビルダーとして使え、互いに置き換え可能です。実行環境として約束しているのは Kubernetes です。

| プラットフォーム | 根拠 |
|---|---|
| Windows 11 + WSL2 Ubuntu 24.04 x86_64 | 実際のイメージビルド、デプロイ、検索、セキュリティ受け入れを実施 |
| ネイティブ Ubuntu 24.04 amd64 | アダプタは利用可能。ベアメタルでの受け入れは別途未実施 |
| Ubuntu 22.04 | 今回は未テスト |
| ネイティブ Windows コンテナ | このアダプタでは非対応 |
| 複数ノードの Kubernetes | 未検証。モデル/取り込みの hostPath は 1 ノードを前提 |

## Windows の準備

systemd を有効にした、専用の Ubuntu 24.04 WSL2 ディストリビューションを使います。PowerShell で `wsl --list --verbose` を確認してください。必要なら `wsl --install -d Ubuntu-24.04` で新しいディストリビューションを入れます。Windows の機能を有効にする際、利用者自身による再起動が必要になることがあります。無関係なディストリビューションを停止したり変更したりしないでください。

クローンは Linux ファイルシステム内（例 `~/Projects/berth`）に置き、ランタイムデータもそこに置きます。ビルド、モデルキャッシュ、稼働中の Pod のために、メモリ約 16〜24 GiB とディスク 40 GiB を目安に確保してください。実際の使用量は環境により異なります。

## 新規インストール

Ubuntu 内で、リポジトリ直下から実行します。

```bash
sudo apt-get update
sudo apt-get install -y python3-venv curl git
python3 -m venv .venv
.venv/bin/pip install PyYAML cryptography huggingface_hub
export PYBIN="$PWD/.venv/bin/python"
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export BERTH_ENTRY_PORT=18765
export BERTH_KUBE_PORT=26443
./deploy/k8s/linux/install-k3s.sh
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
.venv/bin/python tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"
```

基盤インストーラは既存の K3s インストールがあると実行を拒否します。`berth-local` コンテキストを作り、`$BERTH_DATA_DIR/client.yaml` を書き出すのもこのインストーラです。省略した場合、どちらも存在しません。既存クラスタを使う場合はインストーラを省略し、代わりにそのクラスタ用の自分の kubeconfig ファイルを `KUBECONFIG` に export し（別の方法で入れた K3s なら、`/etc/rancher/k3s/k3s.yaml` をユーザーが読めるようにコピーしたもの）、その中に存在するコンテキスト名を `BERTH_CONTEXT` に設定します（`kubectl config get-contexts`）。`ops/linux.sh` はすべての呼び出しで `--context "$BERTH_CONTEXT"` を渡すので、この 2 つは対応している必要があります。`HAN_SOLO_*` の名前は `BERTH_*` 変数の非推奨の別名として引き続き受け付けます（両方設定されていれば `BERTH_*` が優先）。本番環境は決して選ばないでください。公式インストーラは、固定したリリースのバイナリのチェックサムを検証します。Docker Desktop や Podman machine は不要です。

Linux の OCI ビルダーでビルドします。ビルドホストで Docker Engine を使う例:

```bash
docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
docker save berth:rc-amd64 -o berth-amd64.tar
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth      # containerd に保存された参照が表示される
export IMAGE=docker.io/library/berth:rc-amd64
./ops/linux.sh install
./ops/linux.sh connect
```

ネイティブ Linux の Podman でも同等です。ただし取り込まれる参照が異なり、`IMAGE` は `sudo k3s ctr images ls` の表示と完全に一致していなければなりません。一致しないと Pod は `ImagePullBackOff` になります。

```bash
podman build --build-arg APT_UPGRADE=1 \
  --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
  -f deploy/container/Containerfile -t berth:rc-amd64 .
podman save --format docker-archive -o berth-amd64.tar localhost/berth:rc-amd64
sudo k3s ctr images import berth-amd64.tar
sudo k3s ctr images ls | grep berth
export IMAGE=localhost/berth:rc-amd64
```

| ビルダーと受け渡し方法 | `IMAGE` に使う取り込み後の参照 |
|---|---|
| `docker build` + `docker save` | `docker.io/library/berth:rc-amd64` |
| `podman build` + `podman save --format docker-archive` | `localhost/berth:rc-amd64` |
| レジストリ | 完全修飾の pull 参照 |

フォアグラウンドの接続は `http://127.0.0.1:18765` を公開します。同じ環境変数を設定した別のターミナルで `./ops/linux.sh verify` を実行してください。Kubernetes の TLS は、クラスタ内部からの API アクセスのために WSL ノードのインターフェイスで待ち受けます。このコントロールプレーンのポートはホスト/ネットワーク側の制御で保護してください。

初期管理者は `cynovela` です。生成されたパスワードは `$BERTH_DATA_DIR/secrets/admin_password` からローカルで読み取ってください。ログに貼り付けたりコミットしたりしないでください。秘密ファイルと生成された Secret は非公開です。再インストールしても既存のユーザーと鍵は保持されます。本番/デモのデータベースは取り込まれません。

外部の OpenAI 互換または Ollama プロバイダを、既存の設定 API/UI から構成してください。新規の既定値は、使用できないループバックのエンドポイントを指しています。BGE はローカルで動きます。テストプロバイダは決定的な契約用フィクスチャであり、本番用の回答モデルではありません。

セッションを継続させるには [Windows ランチャー](windows-launcher.ja.md)を使ってください。systemd のサービスだけでは WSL は動き続けません。

## 取り込みフォルダの追加

`$BERTH_DATA_DIR/ingest` は API とワーカーの Pod に `/app/ingest` として読み取り専用でマウントされるため、新しいサブフォルダはすぐに見えます。再起動、ロールアウト、PVC や Deployment の変更は不要です。Pod は非 root の uid `10001` で動き、読み取り専用の hostPath は `fsGroup` で所有者を変えられないため、ノード上ですべてのフォルダが辿れて、すべてのファイルが誰でも読める必要があります（ディレクトリは `o+rx`、ファイルは `o+r`）。`render.py` は `ingest/` 自体を `755` にし、ヘルパーはその権限でサブフォルダを作りファイルをコピーします。

```bash
./scripts/add-ingest-folder.sh contracts --from ~/Documents/contracts   # --no-register: フォルダを作るだけ
```

ヘルパーはローカル API を通じて `/app/ingest/<folder>` をソースとして登録し（`connect` か `serve` が動いている必要があり、管理者パスワードを `$BERTH_DATA_DIR/secrets/admin_password` から読みます）、スキャンします。手作業で作ったフォルダは、スキャンの前にディレクトリへ `chmod o+rx`、ファイルへ `chmod o+r` が必要です。その後、「ソースを追加」または Quick Start の GUI フォルダ選択から登録できます。

## 運用と復旧

`ops/linux.sh status`、`verify`、`restart`、`stop`、`start`、`backup`、`restore DUMP --yes` は、明示したコンテキスト/名前空間を対象にします。`connect` は動かし続ける必要があり、選んだ Pod が終了したら再起動しなければなりません。`serve` はその再接続ループを代わりに行います。

```bash
export PG_BACKUP_DIR="$PWD/_oss-rc-backups"
./ops/linux.sh backup
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=0
./ops/linux.sh restore "$PG_BACKUP_DIR/pgdump-TIMESTAMP.dump" --yes
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" \
  scale deploy/cynovela deploy/cynovela-worker --replicas=2
```

ダンプ、チェックサムのサイドカー、対になる secret.key-TIMESTAMP は、保護された保管場所にまとめて保持してください。リストアはまず一時データベースでダンプを検証し、復元先の鍵を確認します。リストアしたワークロードを起動する前に、対になる鍵を復元先の秘密ファイルにコピーし、Kubernetes Secret を更新してください。リストアを直すために鍵を作り直してはいけません。SQL ダンプにはリレーショナルな状態とベクトルが含まれます。モデルと元の取り込みファイルは別途保持する必要があります。

このリリースのリストア予行は、別の名前空間、PostgreSQL の PVC、Redis と、対になる合成鍵を使い、読み取り専用のモデルキャッシュと合成の取り込みファイルは共有しました。これはデータベースの復旧であり、物理的に別のホストでのテストではありません。

## WSL での知見

テストした WSL カーネルは cgroup v1 を公開していました。Kubernetes 1.35 はその環境で、文書化された互換オプション `failCgroupV1: false` を必要とします。インストーラは cgroup v1 の場合に限りこれを書き込みます。新しいホストでは cgroup v2 を推奨します。Windows 全体の .wslconfig の変更は不要です。

テストした containerd のリゾルバには `GODEBUG=netdns=go` が必要で、これは専用の K3s ユニットに限定して設定しています。スーパーバイザーと Kubernetes Service へのアクセスのため、K3s はノードのインターフェイスで待ち受ける必要があります。

参考: [K3s configuration](https://docs.k3s.io/installation/configuration)、[Kubernetes cgroups](https://v1-35.docs.kubernetes.io/docs/concepts/architecture/cgroups/)、[Go resolver](https://github.com/golang/go/blob/master/src/net/net.go)。
