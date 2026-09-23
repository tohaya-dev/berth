param(
  [ValidateSet('start','stop')][string]$Action = 'start',
  [ValidatePattern('^[A-Za-z0-9.-]+$')][string]$Distribution = 'Ubuntu-24.04',
  [Parameter(Mandatory)][ValidatePattern('^/[A-Za-z0-9_./-]+$')][string]$Repository,
  [Parameter(Mandatory)][ValidatePattern('^/[A-Za-z0-9_./-]+$')][string]$Data,
  [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$Context,
  [ValidatePattern('^[a-z0-9-]+$')][string]$Namespace = 'berth',
  [ValidateRange(1024,65535)][int]$Port = 18765
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA ('Berth\' + $Context)
$legacyStateDir = Join-Path $env:LOCALAPPDATA ('HanSolo\' + $Context)
if (-not (Test-Path -LiteralPath $stateDir) -and (Test-Path -LiteralPath $legacyStateDir)) {
  # Compatibility: adopt state (launcher.json, logs) written by the pre-rename launcher so start/stop
  # still recognise a running process. The legacy directory is copied, never deleted.
  New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
  Get-ChildItem -LiteralPath $legacyStateDir -File | Copy-Item -Destination $stateDir -Force
  Write-Output ('Migrated launcher state from ' + $legacyStateDir + ' to ' + $stateDir)
}
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$recordPath = Join-Path $stateDir 'launcher.json'
$existing = $null
if (Test-Path -LiteralPath $recordPath) {
  $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
  $candidate = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
  if ($candidate -and $candidate.ProcessName -eq 'wsl' -and $candidate.StartTime.ToUniversalTime().Ticks -eq $record.startedTicks) { $existing = $candidate }
}
if ($Action -eq 'stop') {
  if ($existing) { Stop-Process -Id $existing.Id }
  if (Test-Path -LiteralPath $recordPath) { Remove-Item -LiteralPath $recordPath }
  Write-Output 'Dedicated launcher stopped; no WSL distribution was terminated.'
  exit 0
}
if ($existing) { Write-Output ('Already running: PID ' + $existing.Id); exit 0 }
# A live Windows WSL client anchors the distribution; systemd services alone do not.
# Parameter validation ensures each generated command-line argument has no whitespace or quotes.
# BERTH_* is the primary name; the HAN_SOLO_* pair is passed as well during the transition (deprecated alias).
$arguments = @('-d',$Distribution,'--exec','env',
  "BERTH_CONTEXT=$Context", "BERTH_NAMESPACE=$Namespace",
  "BERTH_DATA_DIR=$Data", "BERTH_ENTRY_PORT=$Port",
  "HAN_SOLO_CONTEXT=$Context", "HAN_SOLO_NAMESPACE=$Namespace",
  "HAN_SOLO_DATA_DIR=$Data", "HAN_SOLO_ENTRY_PORT=$Port",
  "KUBECONFIG=$Data/client.yaml", '/bin/bash', "$Repository/ops/linux.sh", 'serve')
$launcher = Start-Process -FilePath wsl.exe -ArgumentList $arguments -WindowStyle Hidden -PassThru `
  -RedirectStandardOutput (Join-Path $stateDir 'forward.log') `
  -RedirectStandardError (Join-Path $stateDir 'forward-error.log')
@{pid=$launcher.Id;startedTicks=$launcher.StartTime.ToUniversalTime().Ticks;distribution=$Distribution;context=$Context} | ConvertTo-Json | Set-Content -LiteralPath $recordPath
Write-Output ('Started dedicated WSL launcher: PID ' + $launcher.Id + ', endpoint http://127.0.0.1:' + $Port)
