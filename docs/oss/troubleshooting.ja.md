[English](troubleshooting.md) | 日本語

# トラブルシューティング

各項目は「症状（スクリプトが出すメッセージはそのまま記載）」「原因」「確認コマンド」「対処」の順に並べています。コマンドは [linux-wsl.ja.md](linux-wsl.ja.md) の環境変数を設定したうえで、Ubuntu 内のリポジトリ直下で実行する前提です。

```bash
export BERTH_CONTEXT=berth-local
export BERTH_NAMESPACE=berth
export BERTH_DATA_DIR="$HOME/.local/share/berth"
export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"
export PYBIN="$PWD/.venv/bin/python"
```

以下の確認では短い別名を使います。

```bash
alias kb='kubectl --context "$BERTH_CONTEXT" -n "$BERTH_NAMESPACE"'
```

関連ページ: [Windows ランチャー](windows-launcher.ja.md)、[アンインストールとアップグレード](uninstall-upgrade.ja.md)、[動作要件](requirements.ja.md)、[制限事項](limitations.ja.md)、[FAQ](faq.ja.md)。

## 目次

1. [環境変数が未設定（コンテキスト・名前空間・データディレクトリ）](#1-環境変数が未設定コンテキスト名前空間データディレクトリ)
2. [`install-k3s.sh` が実行を拒否する](#2-install-k3ssh-が実行を拒否する)
3. [モデルのダウンロードに失敗する](#3-モデルのダウンロードに失敗する)
4. [`ops/linux.sh install` が途中で止まる・時間切れになる](#4-opslinuxsh-install-が途中で止まる時間切れになる)
5. [API の Pod が Ready にならない（900 秒の待ち）](#5-api-の-pod-が-ready-にならない900-秒の待ち)
6. [メモリ不足と OOMKilled](#6-メモリ不足と-oomkilled)
7. [ブラウザの接続が切れる](#7-ブラウザの接続が切れる)
8. [WSL が停止し、Pod が再起動した](#8-wsl-が停止しpod-が再起動した)
9. [Windows ランチャーの動作](#9-windows-ランチャーの動作)
10. [バックアップ・リストアのエラー](#10-バックアップリストアのエラー)

---

## 1. 環境変数が未設定（コンテキスト・名前空間・データディレクトリ）

### `Set BERTH_CONTEXT ...`

```text
ops/linux.sh: line 18: CONTEXT: Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to the target Kubernetes context
```

- **原因:** `ops/linux.sh` のすべてのサブコマンドは明示的なコンテキストを必要とし、既定値はありません。別のターミナルで実行した `export` は新しいターミナルには引き継がれません。
- **対処:** このシェルで `BERTH_CONTEXT`（と上の一式）を `export` します。毎回入力しないよう、`export` 行を自分だけが読めるファイルにまとめ、新しいシェルごとに `source` してください。

### `status` に何も出ない、または別の名前空間に別の一式ができた

```text
No resources found in berth namespace.
```

- **原因:** シェルの `BERTH_NAMESPACE` が、インストールした名前空間と違います(例: 独自の値でインストールしたのに、このシェルでは export しておらず、`ops/linux.sh` が既定の `berth` を使っている)。そのため空の名前空間を見ることになります。そのシェルで `install`/`start` を実行すると、その名前空間に**別個の二つ目**のインスタンスが作られます。
- **確認:** `echo "$BERTH_NAMESPACE"`、`kubectl --context "$BERTH_CONTEXT" get namespaces`。
- **対処:** どのサブコマンドの前にも、インストールした名前空間の値で `BERTH_NAMESPACE` を export します。誤って作った名前空間に必要なものが無ければ、[アンインストールとアップグレード](uninstall-upgrade.ja.md#クラスタから-berth-を削除する)の手順で削除できます（破壊的操作）。

### kubeconfig にコンテキストが無い・間違っている

```text
Error in configuration: context was not found for specified context: berth-local
```

- **原因:** `KUBECONFIG` が `BERTH_CONTEXT` を含むファイルを指していません。コンテキストと `$BERTH_DATA_DIR/client.yaml` は `install-k3s.sh` が作ります。既存クラスタを使うためにこれを省略した場合、コンテキスト名はご自身の kubeconfig にある名前です。
- **確認:** `echo "$KUBECONFIG"; kubectl config get-contexts`。
- **対処:** `export KUBECONFIG="$BERTH_DATA_DIR/client.yaml"` とするか、`kubectl config get-contexts` に表示される名前を `BERTH_CONTEXT` に設定します。

### `ops/linux.sh` のその他のメッセージ

| メッセージ | 原因 | 対処 |
|---|---|---|
| `IMAGE: Set IMAGE to a built/imported Berth image` | `install`/`start` にはイメージ参照が必要 | `sudo k3s ctr images ls` の表示どおりに `export IMAGE=...` |
| `Invalid entry address: ...` | `BERTH_ENTRY_ADDRESS` または `$BERTH_DATA_DIR/entry-address` が IPv4 アドレスでない | IPv4 アドレスにするか、設定を外す（既定は `127.0.0.1`） |
| `Usage: linux.sh install\|start\|connect\|...` | サブコマンドが不明または未指定 | `uninstall` サブコマンドはありません。[アンインストールとアップグレード](uninstall-upgrade.ja.md)を参照 |
| `notice: using legacy default data directory ...` | `BERTH_DATA_DIR` も旧名の変数も未設定で、`~/.local/share/berth` が無く、以前の既定ディレクトリがある | `BERTH_DATA_DIR` を明示する（[旧名からの移行](uninstall-upgrade.ja.md#旧名からの移行)） |

## 2. `install-k3s.sh` が実行を拒否する

インストーラは何かを変更する前に前提条件を確認し、終了コード 2（`sudo` の場合は 1）で止まります。

| メッセージ | 原因 | 対処 |
|---|---|---|
| `Set BERTH_DATA_DIR (deprecated alias: HAN_SOLO_DATA_DIR) to a dedicated Linux data directory` | 変数が未設定 | `export BERTH_DATA_DIR="$HOME/.local/share/berth"` |
| `Set BERTH_CONTEXT (deprecated alias: HAN_SOLO_CONTEXT) to a dedicated name` | 変数が未設定 | `export BERTH_CONTEXT=berth-local` |
| `Use a dedicated berth-* context (legacy hansolo-* is still accepted)` | コンテキスト名が `berth-[a-z0-9-]+` に合わない | `berth-local` のように英小文字・数字・ハイフンで付ける |
| 開始直後に*メッセージなし、終了コード 2* | `BERTH_DATA_DIR` が `/mnt/` 配下、`/`、または `$HOME` そのものを指している | Linux ファイルシステム上の専用ディレクトリ（例 `$HOME/.local/share/berth`）を使う |
| `This reference installer was validated on amd64 only` | マシンが x86_64 でない | このインストーラの対象外 |
| `Enable systemd in the dedicated WSL distribution` | PID 1 が systemd でない | ディストリビューションで systemd を有効にし（[Microsoft: WSL の systemd](https://learn.microsoft.com/ja-jp/windows/wsl/systemd)）、そのディストリビューションを再起動して `ps -p 1 -o comm=` で確認 |
| `sudo: a password is required` | インストーラは `sudo -n true` を実行するため、パスワード不要の sudo か、認証済みのキャッシュが必要 | 同じターミナルで `sudo -v` を実行し、すぐにインストーラを起動する |
| `K3s already exists. Reuse the explicitly configured context; this installer will not overwrite it.` | `/usr/local/bin/k3s` または `k3s*` の systemd ユニットが既にある。インストーラは他の K3s を再利用も上書きもしない | [linux-wsl.ja.md](linux-wsl.ja.md) の既存クラスタの手順（そのクラスタ用の `KUBECONFIG` と `BERTH_CONTEXT` を export）を使うか、自分のもので不要なら先に旧 K3s を削除する（[アンインストールとアップグレード](uninstall-upgrade.ja.md#k3s-を削除する)） |

cgroup v1 のカーネルでは、インストーラが kubelet の設定（`failCgroupV1: false`）を自動で書き込みます。これは想定どおりの動作でエラーではありません。[WSL での知見](linux-wsl.ja.md#wsl-での知見)を参照してください。

`install`/`start` から呼ばれる `render.py` もデータディレクトリに同じ規則を適用し、`runtime data must be on the Linux filesystem` と表示します。

## 3. モデルのダウンロードに失敗する

`tools/download-rag-models.py` は `locks/models.json` に固定されたリビジョンだけを取得し、各ファイルのサイズと SHA256 を確認します。

| 出力 | 意味 |
|---|---|
| `BAAI/bge-m3 pinned files verified`（`BAAI/bge-reranker-v2-m3` も同様）の後に `No generative model downloaded` | 成功 |
| `FAIL: model checksum mismatch: <file>` | 取得したファイルのサイズまたはハッシュが固定値と異なる |
| `huggingface_hub` からの Python トレースバック | ダウンロード自体の失敗（ネットワーク、プロキシ、Hugging Face 側の障害） |

- **確認:** 空き容量（`df -h "$BERTH_DATA_DIR"`。固定ファイルは約 4.3 GiB）と `huggingface.co` への接続。
- **不一致の対処:** 該当モデルのフォルダだけを削除し（例 `rm -rf "$BERTH_DATA_DIR/models/models--BAAI--bge-m3"`）、ツールを再実行します。「通すため」に他所からファイルをコピーしないでください。
- このツールは生成（回答）モデルをダウンロードしません。LLM プロバイダはインストール後に別途設定します。

## 4. `ops/linux.sh install` が途中で止まる・時間切れになる

`install`（`start` と同じ）は次の順に進み、最初に失敗した段階で止まります。

| 段階 | 待ち時間の上限 | 失敗したときの確認 |
|---|---|---|
| マニフェスト生成、名前空間/Secret/ConfigMap/PostgreSQL/Redis の適用 | — | `render.py` または `kubectl apply` のエラー表示 |
| PostgreSQL（`cynovela-pgvector`）のロールアウト | 300 秒 | `kb describe pod -l app=cynovela-pgvector` |
| Redis（`cynovela-redis`）のロールアウト | 180 秒 | `kb describe pod -l app=cynovela-redis` |
| 初期化 Job `hansolo-bootstrap` | 300 秒 | `kb logs job/hansolo-bootstrap` |
| API（`cynovela`）のロールアウト | 900 秒 | [5 節](#5-api-の-pod-が-ready-にならない900-秒の待ち)を参照 |
| ワーカー（`cynovela-worker`）のロールアウト | 600 秒 | `kb describe pod -l app=cynovela-worker`、`kb logs deploy/cynovela-worker` |

成功すると最後に `Runtime ready. Run './ops/linux.sh connect' in a terminal; endpoint http://127.0.0.1:18765` と表示されます。

**初期化 Job。** `backoffLimit: 0` で作られるため、一度失敗するとその回は失敗で確定します。出力されるメッセージ:

| ログ | 意味 |
|---|---|
| `Fresh PostgreSQL administrator initialized; no source database imported` | 初回インストール成功 |
| `Existing users retained; bootstrap is a no-op` | 再インストール。ユーザーとパスワードは変更されない |
| `Fresh install requires an initial admin password of at least 12 characters` | `$BERTH_DATA_DIR/secrets/admin_password` が 12 文字未満に書き換えられている |
| `Required PostgreSQL schema was not applied` | スキーマ適用に失敗。直前の行を読む |

`install` は毎回 Job を削除して作り直すので、原因を直したら `./ops/linux.sh install` をもう一度実行するだけです。

**`ImagePullBackOff` / `ErrImagePull`。** Pod は `imagePullPolicy: IfNotPresent` で動きます。`IMAGE` が K3s の containerd に保存された参照と完全に一致しないと、Kubernetes はレジストリから取得しようとして失敗します。`sudo k3s ctr images ls | grep berth` と `kb get deploy cynovela -o jsonpath='{.spec.template.spec.containers[0].image}'` で確認してください。Docker の `save` は `docker.io/library/<name>:<tag>`、Podman の `save --format docker-archive` は `localhost/<name>:<tag>` として取り込まれます（[linux-wsl.ja.md](linux-wsl.ja.md) の表）。正しい `IMAGE` を export して `install` を再実行します。

## 5. API の Pod が Ready にならない（900 秒の待ち）

API には 90 × 10 秒（15 分）の起動猶予があり、`install` はロールアウトを 900 秒待ちます。イメージとモデルが揃っていれば、通常は 1 分程度で起動します。

```bash
kb get pods -o wide
kb describe pod -l app=cynovela          # Events: スケジュール、イメージ、プローブの失敗
kb logs deploy/cynovela --tail=100
kb logs deploy/cynovela --previous --tail=100   # 直前の再起動より前の実行
kb get events --sort-by=.lastTimestamp | tail -20
```

| 見えるもの | 原因 | 対処 |
|---|---|---|
| `Pending`。イベントにメモリや CPU の不足 | ノードがリソース要求を満たせない（API は 1 Pod あたり 2 GiB、ワーカーは 768 MiB を要求） | WSL 内のメモリを空けるか WSL に割り当てを増やす。[6 節](#6-メモリ不足と-oomkilled)を参照 |
| `ImagePullBackOff` | イメージ参照の不一致 | [4 節](#4-opslinuxsh-install-が途中で止まる時間切れになる)を参照 |
| 再起動を繰り返し `CrashLoopBackOff`。不足モデルの一覧の後、ログが `モデル不在のため起動中止 (exit 2)` で終わる | API は `HF_HUB_OFFLINE=1` で動き、モデルを自分ではダウンロードしない。`$BERTH_DATA_DIR/models`（`/app/store/models` に読み取り専用でマウント）に `bge-m3` が無い | `tools/download-rag-models.py --cache "$BERTH_DATA_DIR/models"` を実行（[linux-wsl.ja.md](linux-wsl.ja.md) の手順）。`ls "$BERTH_DATA_DIR"/models/models--BAAI--bge-m3/snapshots/` が空でないことを確認し、`./ops/linux.sh restart` |
| ファイルはあるのに同じ症状 | Pod は uid `10001` で動き、hostPath を読み取り専用で読む。ノード上でディレクトリは `o+rx`、ファイルは `o+r` が必要 | `find "$BERTH_DATA_DIR/models" -type d -exec chmod o+rx {} +` と `find "$BERTH_DATA_DIR/models" -type f -exec chmod o+r {} +` の後、`./ops/linux.sh restart` |
| WSL の再起動直後、`Running` なのに長時間 `0/1` | cgroup v1 での遅いコールドリカバリ | [8 節](#8-wsl-が停止しpod-が再起動した)を参照 |

直した後の `./ops/linux.sh restart` は再び待ちます（API 900 秒、ワーカー 600 秒）。`./ops/linux.sh verify` が `replicas PASS`、`health PASS`、`pgvector PASS` を表示すれば完了です（`connect` か `serve` の実行中である必要があります）。

## 6. メモリ不足と OOMKilled

同梱設定のリソース: API 2 レプリカ ×（要求 2 GiB、上限 6 GiB）、ワーカー 2 レプリカ ×（要求 768 MiB、上限 6 GiB）。PostgreSQL と Redis には上限がありません。[linux-wsl.ja.md](linux-wsl.ja.md) のとおり、WSL には 16〜24 GiB を確保してください。

確認:

```bash
kb get pods                                   # RESTARTS 列
kb get pod -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.containerStatuses[0].lastState.terminated.reason}{" "}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\n"}{end}'
kubectl --context "$BERTH_CONTEXT" describe node | grep -A6 Conditions   # MemoryPressure
free -h                                       # WSL から見えるメモリ
```

- 直前の状態が `OOMKilled`（終了コード 137）なら、コンテナがメモリ上限に達したか、ノードのメモリが尽きています。
- `kubectl top` には K3s の metrics server が必要です。`error: Metrics API not available` と出る場合は上のコマンドを使ってください。
- 直前の状態が `Unknown`、終了コード 255 の場合はメモリ不足では**ありません**。WSL の停止で残る状態です（[8 節](#8-wsl-が停止しpod-が再起動した)）。

対処: WSL のメモリを増やす、ディストリビューション内の他の処理を止める、使わないときは Berth を止める（`./ops/linux.sh stop`）。同梱設定では、マスキングの並列度は 1 に制限済みで、ワーカーは軽量な埋め込みモードで動きます。

## 7. ブラウザの接続が切れる

`http://127.0.0.1:18765` は、1 つの API Pod への `kubectl port-forward` で提供されています。その Pod が置き換わる（再起動、ロールアウト、アップグレード、WSL の再起動）と転送は切れます。そのとき次のようなメッセージが出ます。

```text
error: lost connection to pod
error: error upgrading connection: unable to upgrade connection: pod does not exist
```

- `./ops/linux.sh connect` はそこで終了するので、もう一度起動します。
- `./ops/linux.sh serve`（Windows ランチャーが使うもの）は 3 秒後に自動で転送を張り直します。Pod の置き換え中にこれらの行がログに出るのは想定どおりです。
- **ポートが使用中:** 他のプログラムや古い転送が 18765 を使っていると、新しい転送は待ち受けできません。Ubuntu で `ss -ltnp | grep 18765` で確認し、古い転送を止めるか、`BERTH_ENTRY_PORT`（ランチャーは `-Port`）で別のポートを選びます。
- `ops/linux.sh verify` は `http://127.0.0.1:$BERTH_ENTRY_PORT` を確認するため、転送が動いていないと `curl` のエラーで失敗します。

## 8. WSL が停止し、Pod が再起動した

WSL は、Windows 側のクライアントが保持していないディストリビューションを停止します。systemd のサービスだけでは保持されません。`wsl --terminate`、`wsl --shutdown`、アイドル停止、ホストの再起動の後は次のようになります。

- K3s のユニット `k3s-<context>` はディストリビューションの起動とともに systemd から再開し、Pod も再起動します。`kb get pods` の `RESTARTS` が増え、コンテナの直前の状態は `Unknown`、終了コード 255 になります。
- cgroup v1 のカーネルでは、コールドリカバリに最大 15 分かかることがあります。進んでいる間に K3s を何度も再起動しないでください。走査が最初からやり直しになります。
- 確認: `systemctl status k3s-$BERTH_CONTEXT`、`kb get pods -w`、その後 `./ops/linux.sh verify`。
- [Windows ランチャー](windows-launcher.ja.md)でディストリビューションを動かし続け、停止後はランチャーを再度起動してください。

## 9. Windows ランチャーの動作

| 出力・状況 | 意味 |
|---|---|
| `Started dedicated WSL launcher: PID <n>, endpoint http://127.0.0.1:<port>` | 非表示の `wsl.exe` が `ops/linux.sh serve` を実行中 |
| `Already running: PID <n>` | 記録済みのプロセス（同じ PID、名前 `wsl`、同じ開始時刻）が生きているので、新たには起動しない |
| `Dedicated launcher stopped; no WSL distribution was terminated.` | `-Action stop` は記録済みのプロセスだけを止め、`launcher.json` を削除した。K3s と Pod は動き続ける |
| `Migrated launcher state from ...\HanSolo\<context> to ...\Berth\<context>` | 以前のランチャーの状態を一度だけコピーした。旧フォルダはそのまま残る |
| `\\wsl$\...` からスクリプトを実行すると PowerShell が拒否する | 実行ポリシーが UNC 共有をリモート扱いしている。スクリプトをローカルフォルダにコピーする（[Windows ランチャー](windows-launcher.ja.md)） |
| パラメータ検証エラー | `-Repository` と `-Data` は空白を含まない Linux の絶対パス。`-Context`/`-Namespace` は英小文字・数字・ハイフン |

ログと状態は `%LOCALAPPDATA%\Berth\<context>\` にあります: `launcher.json`、`forward.log`、`forward-error.log`。転送エラー（7 節）は `forward-error.log` に出ます。

- ランチャーは起動中と言うのにページが開かない場合、`forward-error.log` を見てから、Ubuntu 内で同じコンテキストと名前空間を指定して `ops/linux.sh status` を実行します。
- `launcher.json` が既に存在しないプロセスを指している場合（Windows の再起動後など）、`start` は単に新しいランチャーを起動します。
- ランチャーは `KUBECONFIG=<Data>/client.yaml` を渡します。`install-k3s.sh` で作っていないクラスタでは、そのコンテキストを含む kubeconfig をそのパスに置くか、`ops/linux.sh serve` を直接使ってください。

## 10. バックアップ・リストアのエラー

`deploy/k8s/pg-backup.sh`（`ops/linux.sh backup` / `restore` から呼ばれる）のメッセージ:

| メッセージ | 原因 | 対処 |
|---|---|---|
| `FAIL: paired encryption key required` | `$BERTH_DATA_DIR/secrets/secret_key` が無いか空 | `BERTH_DATA_DIR` を確認。鍵は初回の `install` で作られる |
| `FAIL: PostgreSQL pod missing`、またはメッセージなしで終了コード 1 | PostgreSQL の Pod が無い（例: PostgreSQL を 0 にスケールする `ops/linux.sh stop` の後） | バックアップ/リストアの前に `./ops/linux.sh start` で再開。`kb get pods -l app=cynovela-pgvector` で確認 |
| `Usage: pg-backup.sh restore DUMP --yes` | ダンプのパスか確認用の `--yes` が無い | `./ops/linux.sh restore <dump> --yes` |
| `FAIL: nonempty dump and checksum sidecar required` / `FAIL: dump checksum mismatch` | ダンプ横の `.sha256` が無い、またはダンプが変化した | ダンプを `.sha256` と `secret.key-<timestamp>` と一緒にコピーする |
| `FAIL: paired backup key and checksum required` / `FAIL: paired backup key checksum mismatch` | このバックアップの `secret.key-<timestamp>` が無いか改変されている | バックアップ一式を揃えて戻す |
| `FAIL: restore the paired key first; never regenerate it` | 復元先の鍵ファイルが無い | 対になる鍵を `$BERTH_DATA_DIR/secrets/secret_key` にコピーする |
| `FAIL: destination encryption key does not match backup` | 復元先が別の鍵を使っている | 復元したワークロードを起動する前に、対になる鍵を復元先の秘密ファイルにコピーし Kubernetes Secret を更新する（[linux-wsl.ja.md](linux-wsl.ja.md#運用と復旧)）。新しい鍵を作り直さない |
| `FAIL: required tables missing` / `FAIL: pgvector missing` | Berth のデータベースのダンプではない | `ops/linux.sh backup` で作ったダンプを使う |

バックアップ成功時は最後に `backup: <path>`、リストア成功時は `restore: PASS` と表示されます。リストアは必ず、本番のデータベースに触れる前に一時データベースへダンプを読み込んで検証します。
