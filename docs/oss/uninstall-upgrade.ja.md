[English](uninstall-upgrade.md) | 日本語

# 停止・アップグレード・アンインストール

`ops/linux.sh` に `uninstall` サブコマンドはありません。このページでは、スクリプトが提供する機能だけを使って、Berth を止める・アップグレードする・手作業で削除する方法を説明します。**破壊的操作** と記した手順は、バックアップが無ければ元に戻せないデータを削除します。

コマンドは [linux-wsl.ja.md](linux-wsl.ja.md) の環境変数を設定したうえで、Ubuntu 内のリポジトリ直下で実行する前提です。

```bash
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
export PYBIN="$PWD/.venv/bin/python"
```

既定の `berth` 以外の名前空間にインストールした場合は、`BERTH_NAMESPACE` を export してください（[トラブルシューティング](troubleshooting.ja.md#1-環境変数が未設定コンテキスト名前空間データディレクトリ)）。

## どこに何があるか

| 項目 | 場所 | 作成するもの |
|---|---|---|
| データベース（ユーザー、ワークスペース、チャンク、ベクトル） | `$BERTH_DATA_DIR/volumes` 配下の PostgreSQL ボリューム（PVC `cynovela-pg-data`） | `ops/linux.sh install` |
| Redis キューのデータ | `$BERTH_DATA_DIR/volumes` 配下の PVC `cynovela-redis-data` | `ops/linux.sh install` |
| 秘密情報: `admin_password`、`pg_password`、`secret_key` | `$BERTH_DATA_DIR/secrets/`（モード 600） | 初回の `install`。存在する限り作り直されない |
| 生成済みマニフェスト | `$BERTH_DATA_DIR/rendered/` | 毎回の `install`/`start` |
| RAG モデル（約 4.3 GiB） | `$BERTH_DATA_DIR/models/` | `tools/download-rag-models.py` |
| 取り込みフォルダ | `$BERTH_DATA_DIR/ingest/` | 利用者 / `scripts/add-ingest-folder.sh` |
| K3s の状態と kubeconfig | `$BERTH_DATA_DIR/k3s/`、`$BERTH_DATA_DIR/kubeconfig`、`$BERTH_DATA_DIR/client.yaml` | `install-k3s.sh` |
| K3s の本体・サービス・アンインストールスクリプト | `/usr/local/bin/k3s`、`/etc/systemd/system/k3s-<context>.service`（と `.service.d/10-dns.conf`）、`/usr/local/bin/k3s-<context>-uninstall.sh` | 公式 K3s インストーラ経由で `install-k3s.sh` |
| 取り込んだイメージ | K3s の containerd（`sudo k3s ctr images ls`） | `sudo k3s ctr images import` |
| Windows ランチャーの状態 | `%LOCALAPPDATA%\Berth\<context>\` | `ops/windows-wsl.ps1` |
| バックアップ | `$PG_BACKUP_DIR`（既定 `~/dt-backups/k8s-pg`） | `ops/linux.sh backup` |

`$BERTH_DATA_DIR/k3s` と `$BERTH_DATA_DIR/volumes` は root の所有です。

## 停止と再開

Windows ランチャーを止めます（PowerShell。起動時と同じパラメータ。`$linuxHome` は [Windows ランチャー](windows-launcher.ja.md) と同じ）。

```powershell
.\windows-wsl.ps1 -Action stop -Repository "$linuxHome/Projects/berth" `
  -Data "$linuxHome/.local/share/berth" -Context berth-local -Namespace berth
```

記録された自分自身のプロセスだけを止め、`Dedicated launcher stopped; no WSL distribution was terminated.` と表示します。K3s と Pod は動き続けます。

Berth のワークロードを止めます（データは残ります）。

```bash
./ops/linux.sh stop     # API、ワーカー、Redis、PostgreSQL をレプリカ 0 にする
```

K3s 自体は動き続けます。再開するときは同じイメージ参照を指定します。`start` は `install` と同じで、マニフェストを生成し直します。

```bash
kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE" get deploy cynovela \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'   # 現在の IMAGE（stop の前、または今読む）
export IMAGE=<その参照>
./ops/linux.sh start
```

`./ops/linux.sh restart` は API とワーカーを再起動するだけで、`stop` の後にレプリカを戻すことはしません。

Kubernetes ごと止めるには専用ユニットを止めます: `sudo systemctl stop k3s-$BERTH_CONTEXT`。このユニットは有効化されており、ディストリビューションの起動時に再び起動します。K3s の文書によれば、サービスを止めてもコンテナが動き続けることがあり、それらを止めるための `/usr/local/bin/k3s-killall.sh` が用意されています（[K3s: Stopping K3s](https://docs.k3s.io/upgrades/killall)）。このスクリプトはマシン上のすべての K3s サービスを止めます。

## まずバックアップ

アップグレードや削除の前に:

```bash
export PG_BACKUP_DIR="$HOME/berth-backups"
./ops/linux.sh backup
```

ランタイムが起動している必要があります（バックアップには PostgreSQL の Pod が必要です）。バックアップ一式は `pgdump-<ts>.dump`、`pgdump-<ts>.sha256`、`secret.key-<ts>` の 3 ファイルです。保護された場所にまとめて保管してください。ダンプにはリレーショナルデータとベクトルが入っています。モデル（再ダウンロードする）、元の取り込みファイル、`$BERTH_DATA_DIR/secrets/admin_password` と `pg_password` は**入っていません**。リストアは [linux-wsl.ja.md](linux-wsl.ja.md#運用と復旧) を参照してください。

## アップグレード

1. バックアップを取る（上記）。
2. ソースを更新し（例: クローンで `git pull`）、新しいバージョンのリリースノートを読む。
3. [linux-wsl.ja.md](linux-wsl.ja.md) のとおり、**新しいタグ**でイメージをビルドして取り込む。
   ```bash
   docker build --platform linux/amd64 --build-arg APT_UPGRADE=1 \
     --build-arg REQUIREMENTS_FILE=locks/requirements-linux-amd64.txt \
     -f deploy/container/Containerfile -t berth:<new-tag> .
   docker save berth:<new-tag> -o berth-<new-tag>.tar
   sudo k3s ctr images import berth-<new-tag>.tar
   sudo k3s ctr images ls | grep berth
   export IMAGE=docker.io/library/berth:<new-tag>
   ```
4. `locks/models.json` が変わっていれば、`tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` を再実行する。
5. `./ops/linux.sh install` を実行する。
6. 接続し直し（`./ops/linux.sh connect`、または `serve`/ランチャーの自動再接続）、`./ops/linux.sh verify` を実行する。

`install` を再実行したときの動作:

- `$BERTH_DATA_DIR/secrets` の既存ファイルはそのまま使う（無いときだけ作る）。
- 名前空間、Secret、ConfigMap、PostgreSQL、Redis を適用し直す。
- 初期化 Job を削除して再実行する。Job はデータベーススキーマを適用し、ユーザーが既にいるので `Existing users retained; bootstrap is a no-op` と表示する（ユーザーとパスワードは変わらない）。
- 新しい `IMAGE` で API とワーカーの Deployment を適用する。API はローリング更新で入れ替わり（一度に 1 Pod 追加、置き換え先が Ready になるまで既存を削除しない）、その後 `install` は API を最大 900 秒、ワーカーを最大 600 秒待つ。

新しいタグにする理由: Pod は `imagePullPolicy: IfNotPresent` で動き、`IMAGE` が同じだと Deployment が変わらないため新しい Pod が起動しません。同じタグを使い回した場合は、取り込み後に `./ops/linux.sh restart` を実行してください。

元に戻す: 以前の `IMAGE`（削除していなければ `sudo k3s ctr images ls` に残っています）を export して `./ops/linux.sh install` を再実行します。新しいバージョンが旧バージョンでは扱えない形にデータベースを変えていた場合は、手順 1 のバックアップからリストアします。

## アンインストール

この順に進めてください。該当しない手順は飛ばします。

### Windows 側

1. ランチャーを止める（`-Action stop`、上記）。
2. 状態フォルダ `%LOCALAPPDATA%\Berth\<context>` を削除する（PID の記録と転送ログだけ）。以前のランチャーの `%LOCALAPPDATA%\HanSolo\<context>` があれば、それは移動ではなくコピーされたものなので、これも削除して構いません。
3. ローカルにコピーした `windows-wsl.ps1` と、自分で作ったラッパースクリプトを削除する。

### クラスタから Berth を削除する

**破壊的操作。** 名前空間を削除すると PostgreSQL と Redis のボリュームも削除されます。K3s の `local-path` ストレージクラスは回収ポリシー `Delete` なので、`$BERTH_DATA_DIR/volumes` 配下のデータベースファイルも消えます。

```bash
kubectl --context "$BERTH_CONTEXT" get namespaces           # まず名前を確認
kubectl --context "$BERTH_CONTEXT" delete namespace "$BERTH_NAMESPACE"
```

`install-k3s.sh` で作っていないクラスタでは、必要なのはこの手順だけです。その場合はここで終え、クラスタは残してください。

### K3s を削除する

**破壊的操作。** `deploy/k8s/linux/install-k3s.sh` で作った K3s に限ります。クラスタ全体を、その上で動く他のものも含めて削除します。

`install-k3s.sh` は公式 K3s インストーラをサービス名 `k3s-<context>` で使うため、アンインストールスクリプトが `/usr/local/bin/k3s-<context>-uninstall.sh` に置かれます。実行前に中身を読んでください（`less /usr/local/bin/k3s-$BERTH_CONTEXT-uninstall.sh`）。

このスクリプトは `K3S_DATA_DIR` が未設定だと `/var/lib/rancher/k3s` を掃除しますが、このインストールでは K3s のデータを `$BERTH_DATA_DIR/k3s` に置いています。そのパスを渡してください（スクリプトは `sudo` で自分自身を再実行し、この変数を引き継ぎます）。

```bash
K3S_DATA_DIR="$BERTH_DATA_DIR/k3s" /usr/local/bin/k3s-$BERTH_CONTEXT-uninstall.sh
```

K3s とそのコンテナを止め、サービスを無効化・削除し、`/usr/local/bin/k3s`、`kubectl`/`crictl`/`ctr` のリンク、`/etc/rancher/k3s`、`/var/lib/kubelet`、指定したデータディレクトリを削除します。他の `k3s*` サービスがある場合は `Additional k3s services installed, skipping uninstall of k3s` と表示し、本体は残します。

`install-k3s.sh` が書いた DNS 用のドロップインは公式インストーラの管理外なので、自分で削除します。

```bash
sudo rm -rf "/etc/systemd/system/k3s-$BERTH_CONTEXT.service.d"
sudo systemctl daemon-reload
```

### データディレクトリを削除する

**破壊的操作。** 秘密情報、モデル、取り込みフォルダ、生成済みマニフェスト、kubeconfig、残ったボリュームを削除します。まだ必要なもの（バックアップ、バックアップと対になる `secrets/secret_key`、取り込みファイル）は先にコピーしてください。一部は root の所有です。

```bash
echo "$BERTH_DATA_DIR"                 # 意図したディレクトリが表示されること。空は不可
sudo rm -rf -- "$BERTH_DATA_DIR"
```

### 残りのもの

- イメージのアーカイブ（`berth-*.tar`）と、ビルダー側のイメージ（`docker image rm berth:<tag>` または `podman image rm localhost/berth:<tag>`）。
- `$PG_BACKUP_DIR`（既定 `~/dt-backups/k8s-pg`）のバックアップ。不要になってから削除する。
- クローン（`.venv` を含む）。
- WSL ディストリビューションが Berth 専用で他に何も無い場合に限り: PowerShell で `wsl --unregister Ubuntu-24.04`。**破壊的操作:** ディストリビューション全体と中のファイルをすべて削除します。

## 旧名からの移行

以前のバージョンは `HAN_SOLO_*` の変数と `HanSolo` のフォルダ名を使っていました。現在のスクリプトもそれらを受け付けます。

| 以前 | 現在 | 動作 |
|---|---|---|
| `HAN_SOLO_CONTEXT`、`HAN_SOLO_NAMESPACE`、`HAN_SOLO_DATA_DIR`、`HAN_SOLO_ENTRY_PORT`、`HAN_SOLO_ENTRY_ADDRESS` | 同じ接尾辞の `BERTH_*` | `ops/linux.sh` は両方を読む。両方あれば `BERTH_*` が優先 |
| `HAN_SOLO_DATA_DIR`、`HAN_SOLO_CONTEXT`、`HAN_SOLO_KUBE_PORT` | `BERTH_*` | `install-k3s.sh` でも同じ規則 |
| `HAN_SOLO_SECRET_FILE` | `BERTH_SECRET_FILE` | `pg-backup.sh` でも同じ規則。`ops/linux.sh` は両方を設定する |
| 既定のデータディレクトリ `~/.local/share/hansolo` | `~/.local/share/berth` | どちらの変数も未設定で `~/.local/share/berth` が無いときだけ使われ、`ops/linux.sh` が `notice: using legacy default data directory ...` と表示する |
| コンテキスト名 `hansolo-*` | `berth-*` | `install-k3s.sh` は引き続き受け付ける |
| `%LOCALAPPDATA%\HanSolo\<context>` | `%LOCALAPPDATA%\Berth\<context>` | 次回の start か stop でランチャーがファイルをコピーする。旧フォルダは残る |

推奨する移行手順:

1. 自分のスクリプトや環境ファイルの `HAN_SOLO_*` を `BERTH_*` に置き換える。Windows ランチャーは既に両方を渡しています。
2. 既存のデータディレクトリはそのままにし、`BERTH_DATA_DIR` で明示的に指す（notice も出なくなる）。移動や名前変更は**しない**でください。K3s のサービス（`--data-dir`、ローカルボリュームのパス）と生成済みの hostPath マウントが絶対パスで参照しています。
3. 既存のコンテキスト名はそのまま使う。名前を変えると、インストーラが一度だけ作る K3s のサービス名とアンインストールスクリプト名も変わってしまいます。
4. ランチャーを一度起動して `%LOCALAPPDATA%\Berth\<context>\launcher.json` ができたら、`%LOCALAPPDATA%\HanSolo\<context>` を削除する。

一部の Kubernetes オブジェクト名は元の形のままです（例: 初期化 Job `hansolo-bootstrap`、Deployment `cynovela*`）。内部名なので対応は不要です。
