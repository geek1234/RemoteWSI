param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [int]$Port = 18021,
    [int]$StartupTimeoutSeconds = 45,
    [int]$Iterations = 25,
    [int]$Warmup = 3,
    [string]$SlideId = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}

$runScript = Join-Path $PSScriptRoot "run-local-viewer.ps1"
$benchScript = Join-Path $PSScriptRoot "perf_benchmark.py"
if (-not (Test-Path -LiteralPath $runScript)) {
    throw "Missing launcher: $runScript"
}
if (-not (Test-Path -LiteralPath $benchScript)) {
    throw "Missing benchmark script: $benchScript"
}

$rootResolved = (Resolve-Path -LiteralPath $SlideRoot).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$baseUrl = "http://127.0.0.1:$Port"

$argList = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $runScript,
    "-SlideRoot", $rootResolved,
    "-Port", "$Port",
    "-NoReload"
)

Write-Host "Starting local viewer for perf benchmark..." -ForegroundColor Cyan
Write-Host "SlideRoot: $rootResolved" -ForegroundColor DarkGray
Write-Host "Base URL : $baseUrl" -ForegroundColor DarkGray

$proc = Start-Process -FilePath "powershell" -ArgumentList $argList -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

try {
    $health = $null
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq "healthy") { break }
        } catch {
            Start-Sleep -Milliseconds 750
        }
    }

    if ($null -eq $health -or $health.status -ne "healthy") {
        throw "Viewer did not become healthy within $StartupTimeoutSeconds seconds."
    }

    $benchArgs = @(
        "-3.14",
        $benchScript,
        "--base-url", $baseUrl,
        "--iterations", "$Iterations",
        "--warmup", "$Warmup"
    )
    if ($SlideId) {
        $benchArgs += @("--slide-id", $SlideId)
    }

    py @benchArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Performance benchmark failed."
    }

    Write-Host "Performance benchmark passed." -ForegroundColor Green
}
finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }

    try {
        $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($listener) {
            Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
        }
    } catch {
    }
}
