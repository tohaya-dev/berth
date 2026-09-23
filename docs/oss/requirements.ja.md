[English](requirements.md) | 日本語

# 動作要件

[はじめに](start-here.ja.md) や [Linux / WSL2 ガイド](linux-wsl.ja.md) の手順に入る前に、ホストがそろえておくべきものです。値は同梱のスクリプトとマニフェストから取っており、確認し直せるよう設定元のファイル名を併記しています。

## OS とプラットフォーム

| 項目 | 要件 | 設定元 |
|---|---|---|
| ホスト | Windows 11 上の専用 WSL2 Ubuntu 24.04 ディストリビューション、またはネイティブの Ubuntu 24.04(ネイティブ実機での受け入れ確認は未実施) | [linux-wsl.md](linux-wsl.ja.md) のプラットフォーム表 |
| CPU アーキテクチャ | x86_64 / amd64 のみ。それ以外では K3s インストーラーが停止します | `deploy/k8s/linux/install-k3s.sh`(`uname -m` の確認) |
| init | PID 1 が systemd であること(WSL ディストリビューションで systemd を有効化) | `install-k3s.sh`(`ps -p 1` の確認) |
| sudo | 対話なしの sudo: `sudo -n true` が成功すること(パスワードなし、または直前に `sudo` を実行して認証がキャッシュされている状態) | `install-k3s.sh`(`sudo -n true`) |
| Kubernetes | `install-k3s.sh` が入れる K3s `v1.35.5+k3s1`(`K3S_VERSION` で変更可)。1 ディストリビューションに K3s は 1 つ。`/usr/local/bin/k3s` か `k3s*` の systemd ユニットが既にあるとインストーラーは中止します | `install-k3s.sh` |
| 既存クラスター(代替) | 自分が管理する kubeconfig で接続できる本番以外のクラスター。`BERTH_CONTEXT` にはその kubeconfig 内のコンテキスト名を指定 | [linux-wsl.md](linux-wsl.ja.md)「Fresh installation」 |
| データディレクトリ | Linux ファイルシステム上。`/mnt/` 配下、`/`、`$HOME` そのものは不可(既定は `$HOME/.local/share/berth`) | `install-k3s.sh`、`deploy/k8s/linux/render.py`(`/mnt/` を拒否)、`ops/linux.sh`(既定値) |
| cgroup | v2 を推奨。cgroup v1 の場合はインストーラーが Kubernetes の `failCgroupV1: false` を追加します | `install-k3s.sh`、[linux-wsl.md](linux-wsl.ja.md)「WSL findings」 |
| 非対応 | ネイティブ Windows コンテナー。マルチノード Kubernetes は未検証(モデルと取り込みフォルダーは 1 ノード上の hostPath) | [linux-wsl.md](linux-wsl.ja.md) |

## ソフトウェア

| ツール | 用途 | 補足 |
|---|---|---|
| `python3-venv`、`curl`、`git` | インストール手順 | `sudo apt-get install -y python3-venv curl git`([linux-wsl.md](linux-wsl.ja.md)) |
| `PyYAML`、`cryptography`、`huggingface_hub` を入れた Python venv | `render.py`(マニフェストとシークレットの生成)と `tools/download-rag-models.py` | `PYBIN` で `ops/linux.sh` にこのインタープリターを渡します |
| Docker Engine または Podman | `deploy/container/Containerfile` からアプリケーションイメージをビルド(公開イメージはありません) | どのスクリプトも存在を確認しません。ビルド後は `sudo k3s ctr images import` で取り込みます。Docker Desktop や Podman machine は不要です。 |
| `kubectl` | `ops/linux.sh` と `install-k3s.sh` が使用 | K3s のインストールで入ります |

## ポート

| ポート | 待ち受けアドレス | 設定元 | 用途 |
|---|---|---|---|
| 18765 | 既定 `127.0.0.1` | `ops/linux.sh`(`BERTH_ENTRY_PORT`。アドレスは `BERTH_ENTRY_ADDRESS` または 1 行ファイル `$BERTH_DATA_DIR/entry-address`、IPv4 のみ) | `ops/linux.sh connect` / `serve` が張る、`service/cynovela-svc` のポート 8765 へのループバック port-forward。Windows ランチャーも同じ既定値(`-Port 18765`) |
| 26443 | `0.0.0.0`(ノードのインターフェース) | `install-k3s.sh`(`BERTH_KUBE_PORT`、`--bind-address 0.0.0.0`) | Kubernetes API(TLS)。ホストやネットワーク側の制御で保護してください。 |

port-forward は接続元を絞りません。`BERTH_ENTRY_ADDRESS` で待ち受けを広げる場合は、ホストのファイアウォールでポートを制限してください(`ops/linux.sh` のコメント)。K3s はこのほかにも独自のノードポートを使います。[K3s の要件](https://docs.k3s.io/installation/requirements) を参照してください。`connect` / `serve` の前にポート 18765 が空いている必要があります。

## CPU とメモリ

同梱マニフェストが宣言するリソース(`render.py` は変更せずに適用します):

| ワークロード | レプリカ | CPU 要求 | メモリ要求 | メモリ上限 | 設定元 |
|---|---|---|---|---|---|
| API `cynovela` | 2 | 500m | 2Gi | 6Gi | `deploy/k8s/phase4a/20-api-deployment.yaml`(13 行目、83–84 行目) |
| ワーカー `cynovela-worker` | 2 | 250m | 768Mi | 6Gi | `deploy/k8s/phase4a/70-worker-deployment.yaml`(13 行目、57–58 行目) |
| PostgreSQL + pgvector `cynovela-pgvector` | 1 | なし | なし | なし | `deploy/k8s/phase2/50-pgvector.yaml` |
| Redis `cynovela-redis` | 1 | なし | なし | なし | `deploy/k8s/phase2/40-redis.yaml` |
| **合計(API + ワーカー)** | | **1.5 CPU** | **5.5 GiB** | **24 GiB** | |

[linux-wsl.md](linux-wsl.ja.md) の「メモリはおよそ 16–24 GiB を確保」という目安との関係:

- **5.5 GiB** はスケジューラーが予約する量にすぎません。ノードがこれを用意できないと Pod は `Pending` のままになります。PostgreSQL、Redis、K3s 本体、イメージビルドはここに含まれません。
- **24 GiB** は API とワーカーの上限の合計で、カーネルが Pod を強制終了する(OOMKilled、exit 137)までの最悪値です。PostgreSQL と Redis には上限がなく、さらに上乗せされます。
- したがって 16 GiB は、データベース、K3s、ときどきのビルドも同じノードで動かす場合の実用上の下限、24 GiB はアプリケーション Pod 4 つがすべて上限まで使った場合を見込んだ値です。実際の使用量は文書と問い合わせ次第で、このリリースでは Pod ごとの計測はしていません。
- CPU の上限はありません。要求 1.5 CPU に K3s のシステム Pod が加わるため、割り当て可能な CPU は 1.5 より多く必要です。2 以上を見込んでください。
- WSL2 では、既定で Linux VM に Windows のメモリの一部しか割り当てられません(Microsoft の [WSL の設定](https://learn.microsoft.com/ja-jp/windows/wsl/wsl-config) を参照)。Ubuntu から見えるメモリは `free -g` で確認してください。ガイドはグローバルな `.wslconfig` の変更を求めませんが、見込んだメモリが VM に実際に割り当てられている必要があります。

描画済み設定に組み込まれたメモリ対策(`deploy/k8s/linux/render.py`):

- `masking.parallelism = 1`(51 行目)— 伏字処理を 1 件ずつ実行します。並列度を上げた環境でワーカーが OOM による再起動ループに陥った機材向けに推奨されていた設定です。
- `worker.embedding_mode = minimal`(50 行目)。

Pod が `OOMKilled` で再起動を繰り返す場合は、並列度を上げるのではなくノードのメモリを増やしてください。[トラブルシューティング](troubleshooting.ja.md) と [制限事項](limitations.ja.md) も参照してください。

## ディスク

| 項目 | サイズ | 場所 | 根拠 |
|---|---|---|---|
| 埋め込み + リランカーモデル | 4,586,590,960 バイト(約 4.3 GiB) | `$BERTH_DATA_DIR/models` | `locks/models.json` に固定されたファイルの合計 |
| K3s 内のアプリケーションイメージ | 取り込んだタグ 1 つあたり約 2.4–2.5 GiB | `$BERTH_DATA_DIR/k3s` 配下の K3s containerd | 検証ホストでの `sudo k3s ctr images ls` のサイズ列。タグを追加で取り込むたびに増えます |
| イメージビルド | ビルダーのキャッシュと保存した `.tar` アーカイブ | ビルダーの保存領域とカレントディレクトリ | 未計測。取り込み後はアーカイブを削除してください |
| PostgreSQL ボリューム | 要求 2Gi | `$BERTH_DATA_DIR/volumes`(K3s の local-path ストレージ) | `50-pgvector.yaml` の PVC |
| Redis ボリューム | 要求 1Gi | `$BERTH_DATA_DIR/volumes` | `40-redis.yaml` の PVC |
| 取り込む文書、バックアップ | 利用者のデータ量 | `$BERTH_DATA_DIR/ingest`、`PG_BACKUP_DIR` | — |

ガイドの「ディスク 40 GiB」は、モデル、イメージタグ 1〜2 個と pgvector / Redis イメージを含む K3s データディレクトリ、ビルドキャッシュ、取り込みデータとバックアップの余裕を見込んだ値です。使わなくなった古いイメージタグは削除してください。

## ネットワーク

| タイミング | 接続先 |
|---|---|
| K3s のインストール(1 回) | `https://get.k3s.io` と、その公式インストーラーが行う K3s リリースのダウンロード(`install-k3s.sh`) |
| モデルのダウンロード(1 回) | `tools/download-rag-models.py` による Hugging Face(`huggingface.co`)。約 4.3 GiB、リビジョン固定、SHA-256 検証あり |
| イメージビルド | ベースイメージ `python:3.12-slim`、Debian パッケージ、PyPI、`download.pytorch.org`(CPU 版 torch)、GitHub 上の spaCy モデル wheel(`deploy/container/Containerfile`、`locks/requirements-linux-amd64.txt`) |
| 初回の `install` | K3s が `docker.io/pgvector/pgvector:pg16` と `docker.io/library/redis:7.2-alpine` を取得 |
| 実行時 | モデルのダウンロードはしません。イメージは `HF_HUB_OFFLINE=1` と `TRANSFORMERS_OFFLINE=1` を設定し、モデルは読み取り専用でマウントされます。想定される外向き通信は、設定した LLM プロバイダーへの通信だけです。 |

モデルのバージョンとライセンスは [モデルポリシー](model-policy.ja.md) を参照してください。
