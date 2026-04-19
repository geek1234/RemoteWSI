param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [int]$Port = 18081,
    [int]$StartupTimeoutSeconds = 60,
    [string]$SourceName = "",
    [string]$TargetName = ""
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}

$runScript = Join-Path $PSScriptRoot "run-local-viewer.ps1"
if (-not (Test-Path -LiteralPath $runScript)) {
    throw "Missing launcher: $runScript"
}

$existing = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    $owner = Get-Process -Id $existing.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($owner) { $owner.ProcessName } else { "unknown" }
    throw "Port $Port is already in use by PID $($existing.OwningProcess) ($ownerName). Choose another test port."
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

Write-Host "Starting local viewer for color-normalization test..." -ForegroundColor Cyan
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
            Start-Sleep -Milliseconds 700
        }
    }

    if ($null -eq $health -or $health.status -ne "healthy") {
        throw "Viewer did not become healthy within $StartupTimeoutSeconds seconds."
    }

    $slidesResp = Invoke-RestMethod -Uri "$baseUrl/api/slides?limit=500" -TimeoutSec 30
    $nativeSlides = @($slidesResp.items | Where-Object { $_.sourceType -eq "native" })
    if ($nativeSlides.Count -eq 0) {
        throw "No native WSI slides found in: $rootResolved"
    }

    $source = $null
    $target = $null

    if ($SourceName) {
        $source = $nativeSlides | Where-Object { $_.name -like "*$SourceName*" } | Select-Object -First 1
        if ($null -eq $source) {
            throw "SourceName '$SourceName' not found."
        }
    } else {
        $source = $nativeSlides[0]
    }

    if ($TargetName) {
        $target = $nativeSlides | Where-Object { $_.name -like "*$TargetName*" } | Select-Object -First 1
        if ($null -eq $target) {
            throw "TargetName '$TargetName' not found."
        }
    } else {
        if ($nativeSlides.Count -ge 2) {
            $target = $nativeSlides[1]
        } else {
            $target = $source
        }
    }

    $payload = @{
        sourceSlideId = $source.id
        targetSlideId = $target.id
        tau = 0.2
        lam = 5.0
        tileRows = 4
        tileCols = 4
        maxSide = 2048
        preferLowMagnification = $true
    } | ConvertTo-Json

    $result = Invoke-RestMethod -Uri "$baseUrl/api/color-normalization/run" -Method Post -ContentType "application/json" -Body $payload -TimeoutSec 1800
    if (-not $result.ok -or [string]::IsNullOrWhiteSpace($result.resultId)) {
        throw "Color normalization API returned invalid response."
    }

    $artifactMap = $result.artifacts
    $artifactKeys = @("sourcePreview", "targetPreview", "normalizedTiled", "normalizedMono", "differenceHeatmap", "manifest")
    foreach ($key in $artifactKeys) {
        $url = $artifactMap.$key
        if ([string]::IsNullOrWhiteSpace($url)) {
            throw "Missing artifact URL for '$key'."
        }
        $resp = Invoke-WebRequest -Uri "$baseUrl$url" -UseBasicParsing -TimeoutSec 120
        if ($resp.StatusCode -ne 200) {
            throw "Artifact download failed for '$key'."
        }
        Write-Host ("ARTIFACT_OK={0} STATUS={1}" -f $key, $resp.StatusCode)
    }

    Write-Host ("SOURCE_SLIDE={0}" -f $source.name)
    Write-Host ("TARGET_SLIDE={0}" -f $target.name)
    Write-Host ("RESULT_ID={0}" -f $result.resultId)
    Write-Host ("ELAPSED_MS={0}" -f $result.elapsedMs)
    Write-Host "Color normalization checks passed." -ForegroundColor Green
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
