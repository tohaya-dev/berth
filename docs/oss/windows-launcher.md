English | [日本語](windows-launcher.ja.md)

# Windows runtime launcher

WSL systemd services do not by themselves keep a distribution running. Use a live Windows WSL client for the runtime session. The supplied launcher starts a hidden client running `ops/linux.sh serve`, which reconnects the loopback port-forward after pod replacement. It hands the context, namespace, data directory and port to that script as `BERTH_CONTEXT`, `BERTH_NAMESPACE`, `BERTH_DATA_DIR` and `BERTH_ENTRY_PORT`; during the transition it also passes the deprecated `HAN_SOLO_*` aliases so an older checkout of `ops/linux.sh` keeps working.

Copy `ops/windows-wsl.ps1` to a trusted local Windows folder and run it from PowerShell. A script on a WSL UNC share can be treated as remote by RemoteSigned policy; there is no need to change execution policy for this locally reviewed script.

```powershell
$linuxHome = (wsl -d Ubuntu-24.04 -- printenv HOME).Trim()
.\windows-wsl.ps1 -Repository "$linuxHome/Projects/berth" `
  -Data "$linuxHome/.local/share/berth" -Context berth-local -Namespace berth
```

The repository and data parameters accept absolute Linux paths without spaces. Stop any old port-forward on the same loopback port first. Repeating start is idempotent while the recorded launcher process is alive. To stop the launcher, pass the same parameters with `-Action stop`. It checks PID, process name and creation time before stopping its own process. It never terminates a distribution or changes a global WSL setting.

The launcher stores only PID metadata and connection logs under the Windows user's LocalAppData/Berth/context directory. An existing LocalAppData/HanSolo/context directory from an earlier launcher is picked up automatically: its files are copied into the Berth directory on the next start or stop, and the old directory is left in place. No credentials are passed on its command line. Kubernetes credentials remain in the private Linux data directory.

After a deliberate `wsl --terminate Ubuntu-24.04`, run the launcher again. The dedicated K3s unit starts through systemd and recovers its persistent volumes. On the tested cgroup-v1 host, cAdvisor checks stale container scopes serially during cold recovery; allow up to 15 minutes before diagnosing a timeout. Do not repeatedly restart K3s while it is making progress, since this restarts the scan.

Windows host reboot remains a manual test: close/save unrelated work, reboot only when appropriate, invoke this launcher, run `ops/linux.sh verify` with the configured environment, and repeat authenticated RAG and the row/vector consistency check. This RC run does not claim a Windows host reboot test.

References: [Microsoft WSL systemd lifecycle](https://learn.microsoft.com/en-us/windows/wsl/systemd), [cAdvisor containerd task retry logic](https://github.com/k3s-io/cadvisor/blob/v0.52.1/container/containerd/handler.go).
