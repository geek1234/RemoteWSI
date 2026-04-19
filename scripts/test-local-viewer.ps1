param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [int]$Port = 8011,
    [int]$StartupTimeoutSeconds = 45,
    [switch]$CheckAllSlides
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}

$runScript = Join-Path $PSScriptRoot "run-local-viewer.ps1"
if (-not (Test-Path -LiteralPath $runScript)) {
    throw "Missing launcher: $runScript"
}

$rootResolved = (Resolve-Path -LiteralPath $SlideRoot).Path
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

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

Write-Host "Starting local viewer for test..." -ForegroundColor Cyan
Write-Host "SlideRoot: $rootResolved" -ForegroundColor DarkGray
Write-Host "Port     : $Port" -ForegroundColor DarkGray

$proc = Start-Process -FilePath "powershell" -ArgumentList $argList -WorkingDirectory $repoRoot -PassThru -WindowStyle Hidden

try {
    $health = $null
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
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

    $slides = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/slides?limit=200" -TimeoutSec 30
    $items = @($slides.items)
    if ($items.Count -eq 0) {
        throw "No slides discovered under: $rootResolved"
    }

    $svsCount = (@($items | Where-Object { $_.name -match "\.svs$" })).Count
    $jpgCount = (@($items | Where-Object { $_.name -match "\.jpe?g$" })).Count

    Write-Host ("HEALTH=healthy")
    Write-Host ("TOTAL={0}" -f $slides.total)
    Write-Host ("SVS_COUNT={0}" -f $svsCount)
    Write-Host ("JPG_COUNT={0}" -f $jpgCount)

    if ($CheckAllSlides) {
        $toCheck = $items
    } else {
        $toCheck = @($items[0])
    }

    foreach ($slide in $toCheck) {
        $id = [uri]::EscapeDataString($slide.id)

        $meta = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/meta/$id" -TimeoutSec 30
        $thumb = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/thumb/$id" -UseBasicParsing -TimeoutSec 30
        $dzi = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/dzi/$id.dzi" -UseBasicParsing -TimeoutSec 30

        if ($null -eq $meta.id -or $thumb.StatusCode -ne 200 -or $dzi.StatusCode -ne 200) {
            throw "Endpoint check failed for slide: $($slide.name)"
        }

        Write-Host ("SLIDE={0} META_OK=True THUMB={1} DZI={2}" -f $slide.name, $thumb.StatusCode, $dzi.StatusCode)
    }

    Write-Host "All checks passed." -ForegroundColor Green
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
