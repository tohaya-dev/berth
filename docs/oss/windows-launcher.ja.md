[English](windows-launcher.md) | 日本語

# Windows ランタイムランチャー

WSL の systemd サービスは、それだけではディストリビューションを動かし続けません。ランタイムのセッションには、動作中の Windows 側 WSL クライアントを使ってください。同梱のランチャーは、`ops/linux.sh serve` を実行する非表示のクライアントを起動します。`serve` は Pod が置き換わった後にループバックのポート転送を張り直します。ランチャーはコンテキスト、名前空間、データディレクトリ、ポートを `BERTH_CONTEXT`、`BERTH_NAMESPACE`、`BERTH_DATA_DIR`、`BERTH_ENTRY_PORT` としてこのスクリプトに渡します。移行期間中は、古いチェックアウトの `ops/linux.sh` も動くよう、非推奨の `HAN_SOLO_*` 別名も渡します。

`ops/windows-wsl.ps1` を信頼できるローカルの Windows フォルダにコピーし、PowerShell から実行します。WSL の UNC 共有上のスクリプトは、RemoteSigned ポリシーでリモート扱いされることがあります。ローカルで内容を確認したこのスクリプトのために、実行ポリシーを変更する必要はありません。

```powershell
$linuxHome = (wsl -d Ubuntu-24.04 -- printenv HOME).Trim()
.\windows-wsl.ps1 -Repository "$linuxHome/Projects/berth" `
  -Data "$linuxHome/.local/share/berth" -Context berth-local -Namespace berth
```

リポジトリとデータのパラメータには、空白を含まない Linux の絶対パスを指定します。先に、同じループバックポートの古いポート転送を止めてください。記録されたランチャープロセスが生きている間は、start を繰り返しても結果は変わりません（冪等）。ランチャーを止めるには、同じパラメータに `-Action stop` を付けて実行します。止める前に PID、プロセス名、作成時刻を確認し、自分自身のプロセスだけを止めます。ディストリビューションを終了させたり、WSL 全体の設定を変更したりすることはありません。

ランチャーが保存するのは、Windows ユーザーの LocalAppData/Berth/context ディレクトリにある PID のメタデータと接続ログだけです。以前のランチャーが作った LocalAppData/HanSolo/context ディレクトリがあれば自動的に引き継ぎます。次回の start か stop でそのファイルを Berth ディレクトリにコピーし、古いディレクトリはそのまま残します。コマンドラインで資格情報を渡すことはありません。Kubernetes の資格情報は非公開の Linux データディレクトリに置かれたままです。

意図的に `wsl --terminate Ubuntu-24.04` を実行した後は、ランチャーを再度実行してください。専用の K3s ユニットが systemd から起動し、永続ボリュームを回復します。テストした cgroup v1 のホストでは、コールドリカバリ中に cAdvisor が古いコンテナスコープを一つずつ確認するため、タイムアウトと判断する前に最大 15 分待ってください。進んでいる間に K3s を何度も再起動しないでください。走査が最初からやり直しになります。

Windows ホストの再起動は手動テストのままです。無関係な作業を閉じて保存し、適切なときにだけ再起動し、このランチャーを起動し、設定した環境で `ops/linux.sh verify` を実行し、認証付きの RAG と行/ベクトルの整合性確認をもう一度行ってください。この RC の実行では、Windows ホスト再起動のテストは実施済みと主張していません。

参考: [Microsoft WSL systemd lifecycle](https://learn.microsoft.com/en-us/windows/wsl/systemd)、[cAdvisor containerd task retry logic](https://github.com/k3s-io/cadvisor/blob/v0.52.1/container/containerd/handler.go)。
