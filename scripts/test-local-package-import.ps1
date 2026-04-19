param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [Parameter(Mandatory = $true)]
    [string]$PackagePath,

    [int]$Port = 8011,
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}
if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "PackagePath does not exist: $PackagePath"
}

$runScript = Join-Path $PSScriptRoot "run-local-viewer.ps1"
if (-not (Test-Path -LiteralPath $runScript)) {
    throw "Missing launcher: $runScript"
}

$rootResolved = (Resolve-Path -LiteralPath $SlideRoot).Path
$pkgResolved = (Resolve-Path -LiteralPath $PackagePath).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$baseUrl = "http://127.0.0.1:$Port"

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

Write-Host "Starting local viewer for package-import test..." -ForegroundColor Cyan
Write-Host "SlideRoot : $rootResolved" -ForegroundColor DarkGray
Write-Host "Package   : $pkgResolved" -ForegroundColor DarkGray
Write-Host "Base URL  : $baseUrl" -ForegroundColor DarkGray

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

    $headers = @{
        "Content-Type" = "application/octet-stream"
        "X-Package-Name" = (Split-Path -Leaf $pkgResolved)
    }

    $importResp = Invoke-RestMethod -Uri "$baseUrl/api/packages/import" -Method Post -InFile $pkgResolved -Headers $headers -TimeoutSec 600
    if (-not $importResp.ok -or [string]::IsNullOrWhiteSpace($importResp.slideId)) {
        throw "Package import API returned invalid response."
    }

    $slideId = [uri]::EscapeDataString($importResp.slideId)
    $meta = Invoke-RestMethod -Uri "$baseUrl/api/meta/$slideId" -TimeoutSec 30
    $thumb = Invoke-WebRequest -Uri "$baseUrl/api/thumb/$slideId" -UseBasicParsing -TimeoutSec 30
    $dzi = Invoke-WebRequest -Uri "$baseUrl/dzi/$slideId.dzi" -UseBasicParsing -TimeoutSec 30
    $pkg = Invoke-WebRequest -Uri "$baseUrl/api/slides/$slideId/package" -UseBasicParsing -TimeoutSec 60

    if ($thumb.StatusCode -ne 200 -or $dzi.StatusCode -ne 200 -or $pkg.StatusCode -ne 200) {
        throw "Package slide endpoint check failed."
    }

    Write-Host ("IMPORT_OK=True")
    Write-Host ("SLIDE_ID={0}" -f $importResp.slideId)
    Write-Host ("FILE_NAME={0}" -f $importResp.fileName)
    Write-Host ("SOURCE_TYPE={0}" -f $importResp.sourceType)
    Write-Host ("META_NAME={0}" -f $meta.name)
    Write-Host ("THUMB_STATUS={0}" -f $thumb.StatusCode)
    Write-Host ("DZI_STATUS={0}" -f $dzi.StatusCode)
    Write-Host ("PACKAGE_DOWNLOAD_STATUS={0}" -f $pkg.StatusCode)
    Write-Host "Package import checks passed." -ForegroundColor Green
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
