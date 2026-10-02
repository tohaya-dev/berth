param(
  [ValidateSet('start','stop')][string]$Action = 'start',
  [ValidatePattern('^[A-Za-z0-9.-]+$')][string]$Distribution = 'Ubuntu-24.04',
  [Parameter(Mandatory)][ValidatePattern('^/[A-Za-z0-9_./-]+$')][string]$Repository,
  [Parameter(Mandatory)][ValidatePattern('^/[A-Za-z0-9_./-]+$')][string]$Data,
  [Parameter(Mandatory)][ValidatePattern('^[a-z0-9-]+$')][string]$Context,
  [ValidatePattern('^[a-z0-9-]+$')][string]$Namespace = 'hansolo',
  [ValidateRange(1024,65535)][int]$Port = 18765
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA ('HanSolo\' + $Context)
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
$arguments = @('-d',$Distribution,'--exec','env',
  "HAN_SOLO_CONTEXT=$Context", "HAN_SOLO_NAMESPACE=$Namespace",
  "HAN_SOLO_DATA_DIR=$Data", "HAN_SOLO_ENTRY_PORT=$Port",
  "KUBECONFIG=$Data/client.yaml", '/bin/bash', "$Repository/ops/linux.sh", 'serve')
# Start-Process inherits the Windows OpenSSH session job. When this launcher is invoked over SSH,
# Windows terminates the child as soon as that session closes. Win32_Process.Create starts the same
# validated command outside that job, so the WSL client continues to anchor the distribution.
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$commandLine = '"' + $wsl + '" ' + ($arguments -join ' ')
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$commandLine}
if ($created.ReturnValue -ne 0 -or -not $created.ProcessId) {
  throw ('Unable to create detached WSL launcher; Win32_Process.Create returned ' + $created.ReturnValue)
}
$launcher = Get-Process -Id ([int]$created.ProcessId)
@{pid=$launcher.Id;startedTicks=$launcher.StartTime.ToUniversalTime().Ticks;distribution=$Distribution;context=$Context} | ConvertTo-Json | Set-Content -LiteralPath $recordPath
Write-Output ('Started dedicated WSL launcher: PID ' + $launcher.Id + ', endpoint http://127.0.0.1:' + $Port)
