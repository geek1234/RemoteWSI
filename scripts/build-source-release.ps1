param(
    [string]$OutputDir = "dist\\open-source",
    [string]$DateTag = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $DateTag) {
    $DateTag = Get-Date -Format "yyyyMMdd"
}

$releaseRoot = Join-Path $repoRoot $OutputDir
$staging = Join-Path $releaseRoot "wsi-viewer-main-source"
$zipPath = Join-Path $releaseRoot "wsi-viewer-main-source-$DateTag.zip"

if (Test-Path -LiteralPath $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
}
New-Item -ItemType Directory -Path $staging -Force | Out-Null

$items = @(
    "app",
    "assets",
    "docs",
    "scripts",
    ".dockerignore",
    ".gitignore",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "Dockerfile",
    "docker-compose.yml",
    "config.example.yml",
    "LICENSE",
    "README.md",
    "README_LOCAL.md",
    "pyproject.toml",
    "uv.lock"
)

foreach ($item in $items) {
    $src = Join-Path $repoRoot $item
    if (Test-Path -LiteralPath $src) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $staging $item) -Recurse -Force
    }
}

$artifactsDir = Join-Path $staging "scripts\\artifacts"
if (Test-Path -LiteralPath $artifactsDir) {
    Remove-Item -LiteralPath $artifactsDir -Recurse -Force
}

if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}
Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $zipPath -Force

Write-Host "Source release generated." -ForegroundColor Green
Write-Host "Staging: $staging" -ForegroundColor DarkGray
Write-Host "Zip    : $zipPath" -ForegroundColor DarkGray
