param(
    [Parameter(Mandatory = $true)]
    [string]$RemoteBaseUrl,

    [string]$SlideId = "",
    [int]$Limit = 20,
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RemoteBaseUrl)) {
    throw "RemoteBaseUrl is required."
}

$baseUrl = $RemoteBaseUrl.Trim().TrimEnd("/")
if (-not $baseUrl.StartsWith("http://", [System.StringComparison]::OrdinalIgnoreCase) -and
    -not $baseUrl.StartsWith("https://", [System.StringComparison]::OrdinalIgnoreCase)) {
    $baseUrl = "http://$baseUrl"
}

function Get-BodyLength {
    param([Parameter(Mandatory = $true)]$Response)

    try {
        if ($Response.RawContentStream -and $Response.RawContentStream.Length -gt 0) {
            return [int64]$Response.RawContentStream.Length
        }
    } catch {
    }

    if ($Response.Content) {
        return [System.Text.Encoding]::UTF8.GetByteCount([string]$Response.Content)
    }

    return 0
}

Write-Host "Remote stream test target: $baseUrl" -ForegroundColor Cyan

$health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec $TimeoutSec
Write-Host ("HEALTH_STATUS={0}" -f $health.status)

$slides = Invoke-RestMethod -Uri "$baseUrl/api/slides?limit=$Limit" -TimeoutSec $TimeoutSec
$items = @($slides.items)
if ($items.Count -eq 0) {
    throw "No slides found from remote endpoint: $baseUrl/api/slides"
}

$target = $null
if (-not [string]::IsNullOrWhiteSpace($SlideId)) {
    $target = $items | Where-Object { $_.id -eq $SlideId } | Select-Object -First 1
    if ($null -eq $target) {
        throw "SlideId '$SlideId' not found in first $Limit slides."
    }
} else {
    $target = $items[0]
}

$targetId = [string]$target.id
$targetName = [string]$target.name
$targetIdEscaped = [uri]::EscapeDataString($targetId)

Write-Host ("TARGET_SLIDE_ID={0}" -f $targetId)
Write-Host ("TARGET_SLIDE_NAME={0}" -f $targetName)

$meta = Invoke-RestMethod -Uri "$baseUrl/api/meta/$targetIdEscaped" -TimeoutSec $TimeoutSec
Write-Host ("META_WIDTH={0}" -f $meta.width)
Write-Host ("META_HEIGHT={0}" -f $meta.height)
Write-Host ("META_LEVELS={0}" -f $meta.levelCount)
Write-Host ("META_SOURCE={0}" -f $meta.sourceType)

$dziResp = Invoke-WebRequest -Uri "$baseUrl/dzi/$targetIdEscaped.dzi" -UseBasicParsing -TimeoutSec $TimeoutSec
$dziBytes = Get-BodyLength -Response $dziResp
Write-Host ("DZI_STATUS={0}" -f $dziResp.StatusCode)
Write-Host ("DZI_BYTES={0}" -f $dziBytes)

$tileUrls = @(
    "$baseUrl/dzi/$targetIdEscaped`_files/0/0_0.jpeg"
)

try {
    $levelCount = [int]$meta.levelCount
    if ($levelCount -gt 1) {
        $maxLevel = $levelCount - 1
        $tileUrls += "$baseUrl/dzi/$targetIdEscaped`_files/$maxLevel/0_0.jpeg"
    }
} catch {
}

$totalTileBytes = 0
$okTileCount = 0
foreach ($tileUrl in ($tileUrls | Select-Object -Unique)) {
    try {
        $tileResp = Invoke-WebRequest -Uri $tileUrl -UseBasicParsing -TimeoutSec $TimeoutSec
        $tileBytes = Get-BodyLength -Response $tileResp
        $totalTileBytes += $tileBytes
        $okTileCount += 1
        Write-Host ("TILE_OK={0} BYTES={1}" -f $tileUrl, $tileBytes)
    } catch {
        Write-Warning ("Tile request failed: {0}" -f $tileUrl)
    }
}

if ($okTileCount -eq 0) {
    throw "All tile requests failed."
}

$totalDownloaded = $dziBytes + $totalTileBytes
Write-Host ("TOTAL_DOWNLOADED_BYTES={0}" -f $totalDownloaded) -ForegroundColor Green
Write-Host "Remote streaming checks passed." -ForegroundColor Green
