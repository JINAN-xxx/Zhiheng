# Start the existing WSL platform without tying its lifetime to this terminal.
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Distribution = 'Ubuntu-22.04',
    [ValidatePattern('^/[A-Za-z0-9_./-]+$')]
    [string]$LinuxProject = '/opt/zhiheng',
    [switch]$Stop
)
$ErrorActionPreference = 'Stop'
$platformKeeper = "$LinuxProject/tools/keep-platform-wsl.sh"
$platformWsl = Join-Path $env:WINDIR 'System32\wsl.exe'
if ($Stop) {
    & $platformWsl --distribution $Distribution --exec bash $platformKeeper stop
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop the platform host.' }
    Write-Output 'Platform background host stopped.'
    exit 0
}
$platformExisting = @(Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($platformKeeper) -and
    $_.CommandLine.Contains($Distribution) -and -not $_.CommandLine.Contains(' stop')
})
$platformKeeperRunning = $false
if ($platformExisting.Count) {
    & $platformWsl --distribution $Distribution --exec bash $platformKeeper status | Out-Null
    $platformKeeperRunning = $LASTEXITCODE -eq 0
}
if (-not $platformKeeperRunning) {
    $platformLogDirectory = Join-Path $env:TEMP 'zhiheng-platform-host'
    New-Item -ItemType Directory -Path $platformLogDirectory -Force | Out-Null
    $platformProcess = Start-Process -FilePath $platformWsl -ArgumentList @(
        '--distribution', $Distribution, '--exec', 'bash', $platformKeeper, 'start'
    ) -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput (Join-Path $platformLogDirectory 'stdout.log') `
      -RedirectStandardError (Join-Path $platformLogDirectory 'stderr.log')
    Write-Output "Platform background host started (Windows PID $($platformProcess.Id))."
}
for ($platformAttempt = 0; $platformAttempt -lt 30; $platformAttempt++) {
    try {
        $platformHealth = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:19080/health/ready' -TimeoutSec 3
        if ($platformHealth.StatusCode -eq 200) {
            & $platformWsl --distribution $Distribution --exec bash $platformKeeper status | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Write-Output 'Platform ready: http://127.0.0.1:19080'
                exit 0
            }
        }
    } catch { }
    Start-Sleep -Seconds 2
}
throw 'Platform is still starting. Inspect %TEMP%\zhiheng-platform-host\stderr.log.'
