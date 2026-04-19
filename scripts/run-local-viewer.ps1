param(
    [Parameter(Mandatory = $true)]
    [string]$SlideRoot,

    [int]$Port = 8011,
    [Alias("Host")]
    [string]$BindHost = "127.0.0.1",
    [string]$PackageRoot = "",
    [string]$Extensions = ".svs,.tif,.tiff,.ndpi,.mrxs,.scn,.bif,.vms,.vmu,.svslide,.jpg,.jpeg,.png,.bmp",
    [int]$ScanCacheSeconds = 15,
    [switch]$Reload,
    [switch]$NoReload,
    [switch]$AutoPort
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SlideRoot)) {
    throw "SlideRoot does not exist: $SlideRoot"
}

$rootResolved = (Resolve-Path -LiteralPath $SlideRoot).Path

Write-Host "Starting local WSI viewer..." -ForegroundColor Cyan
Write-Host "Root: $rootResolved" -ForegroundColor DarkGray

$env:WSI_LOCAL_ROOT = $rootResolved
$env:WSI_LOCAL_EXTENSIONS = $Extensions
$env:WSI_LOCAL_SCAN_CACHE_SECONDS = [string]$ScanCacheSeconds

if ($PackageRoot) {
    if (-not (Test-Path -LiteralPath $PackageRoot)) {
        New-Item -ItemType Directory -Path $PackageRoot -Force | Out-Null
    }
    $pkgResolved = (Resolve-Path -LiteralPath $PackageRoot).Path
    $env:WSI_LOCAL_PACKAGE_ROOT = $pkgResolved
    Write-Host "PackageRoot: $pkgResolved" -ForegroundColor DarkGray
} else {
    if (Test-Path Env:WSI_LOCAL_PACKAGE_ROOT) {
        Remove-Item Env:WSI_LOCAL_PACKAGE_ROOT -ErrorAction SilentlyContinue
    }
}

if ($Reload -and $NoReload) {
    throw "Use only one flag: -Reload or -NoReload"
}

function Get-PortListener {
    param([int]$CandidatePort)
    return Get-NetTCPConnection -State Listen -LocalPort $CandidatePort -ErrorAction SilentlyContinue | Select-Object -First 1
}

$selectedPort = $Port
$listener = Get-PortListener -CandidatePort $selectedPort
if ($listener) {
    $ownerProc = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    $ownerName = if ($ownerProc) { $ownerProc.ProcessName } else { "unknown" }

    if ($AutoPort) {
        $start = $selectedPort + 1
        $end = $selectedPort + 100
        $found = $null
        for ($candidate = $start; $candidate -le $end; $candidate++) {
            if (-not (Get-PortListener -CandidatePort $candidate)) {
                $found = $candidate
                break
            }
        }
        if ($null -eq $found) {
            throw "Port $selectedPort is in use by PID $($listener.OwningProcess) ($ownerName), and no free port was found in [$start, $end]."
        }
        Write-Host "Port $selectedPort is in use by PID $($listener.OwningProcess) ($ownerName), switched to $found." -ForegroundColor Yellow
        $selectedPort = $found
    } else {
        throw "Port $selectedPort is already in use by PID $($listener.OwningProcess) ($ownerName). Use -Port <new_port> or add -AutoPort."
    }
}

Write-Host "URL : http://$BindHost`:$selectedPort" -ForegroundColor DarkGray

$uvicornArgs = @(
    "-m", "uvicorn",
    "app.local_main:app",
    "--host", $BindHost,
    "--port", [string]$selectedPort
)

# Default is no-reload for stable local benchmark runs.
if ($Reload -and (-not $NoReload)) {
    $uvicornArgs += "--reload"
}

python @uvicornArgs
