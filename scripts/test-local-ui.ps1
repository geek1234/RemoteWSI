param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [int]$Port = 8011,
    [int]$StartupTimeoutSeconds = 45,
    [string]$SearchText = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}

$runScript = Join-Path $PSScriptRoot "run-local-viewer.ps1"
$uiScript = Join-Path $PSScriptRoot "ui_smoke_test.py"
if (-not (Test-Path -LiteralPath $runScript)) {
    throw "Missing launcher: $runScript"
}
if (-not (Test-Path -LiteralPath $uiScript)) {
    throw "Missing UI test script: $uiScript"
}

$rootResolved = (Resolve-Path -LiteralPath $SlideRoot).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$baseUrl = "http://127.0.0.1:$Port"
$screenshotPath = Join-Path $PSScriptRoot "artifacts\\ui-smoke.png"

$existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $owner = Get-Process -Id $existing.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($owner) { $owner.ProcessName } else { "unknown" }
    throw "Port $Port is already in use by PID $($existing.OwningProcess) ($ownerName). Choose another test port."
}

$argList = @(
    "-ExecutionPolicy", "Bypass",
    "-File", $runScript,
    "-SlideRoot", $rootResolved,
    "-Port", "$Port",
    "-NoReload"
)

Write-Host "Starting local viewer for UI test..." -ForegroundColor Cyan
Write-Host "SlideRoot: $rootResolved" -ForegroundColor DarkGray
Write-Host "Base URL : $baseUrl" -ForegroundColor DarkGray

$proc = Start-Process -FilePath "powershell" -ArgumentList $argList -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

try {
    $health = $null
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2
            if ($health.status -eq "healthy") {
                break
            }
        } catch {
            Start-Sleep -Milliseconds 750
        }
    }

    if ($null -eq $health -or $health.status -ne "healthy") {
        throw "Viewer did not become healthy within $StartupTimeoutSeconds seconds."
    }

    $pyVersionCmd = "import playwright, sys; print(sys.version)"
    py -3.14 -c $pyVersionCmd | Out-Null

    $uiArgs = @(
        "-3.14",
        $uiScript,
        "--base-url", $baseUrl,
        "--screenshot-path", $screenshotPath
    )
    if ($SearchText) {
        $uiArgs += @("--search-text", $SearchText)
    }

    py @uiArgs
    if ($LASTEXITCODE -ne 0) {
        throw "UI smoke test failed."
    }

    Write-Host "UI smoke test passed." -ForegroundColor Green
    Write-Host "Screenshot: $screenshotPath" -ForegroundColor DarkGray
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
