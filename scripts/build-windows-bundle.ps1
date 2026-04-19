param(
    [string]$PythonVersion = "3.14",
    [string]$OutputDir = "dist\\windows",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $repoRoot

function Invoke-Py {
    param([string[]]$CmdArgs)
    $versionArg = "-$PythonVersion"
    & py $versionArg @CmdArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: py $versionArg $($CmdArgs -join ' ')"
    }
}

Write-Host "Preparing Windows bundle build..." -ForegroundColor Cyan
Invoke-Py -CmdArgs @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel", "pyinstaller")

Write-Host "Running PyInstaller..." -ForegroundColor Cyan
Invoke-Py -CmdArgs @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--name", "LocalWSIViewer",
    "--collect-all", "openslide",
    "--collect-all", "openslide_bin",
    "--collect-all", "cv2",
    "--collect-submodules", "app",
    "--add-data", "app\\templates;app\\templates",
    "--add-data", "app\\static;app\\static",
    "app\\local_launcher.py"
)

$pyDist = Join-Path $repoRoot "dist\\LocalWSIViewer"
if (-not (Test-Path -LiteralPath $pyDist)) {
    throw "PyInstaller output not found: $pyDist"
}

$bundleRoot = Join-Path $repoRoot $OutputDir
$bundleDir = Join-Path $bundleRoot "LocalWSIViewer"
if (Test-Path -LiteralPath $bundleDir) {
    Remove-Item -LiteralPath $bundleDir -Recurse -Force
}
New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
Copy-Item -LiteralPath $pyDist -Destination $bundleDir -Recurse -Force

Copy-Item -LiteralPath (Join-Path $repoRoot "LICENSE") -Destination (Join-Path $bundleDir "LICENSE") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "README_LOCAL.md") -Destination (Join-Path $bundleDir "README_LOCAL.md") -Force

$thirdPartyRoot = Join-Path $bundleDir "THIRD_PARTY_LICENSES"
New-Item -ItemType Directory -Path $thirdPartyRoot -Force | Out-Null

try {
    $sitePackages = & py "-$PythonVersion" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    if ($LASTEXITCODE -eq 0 -and $sitePackages) {
        $distInfo = Get-ChildItem -Path (Join-Path $sitePackages "openslide_bin-*.dist-info") -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($distInfo) {
            $licenseSrc = Join-Path $distInfo.FullName "licenses"
            if (Test-Path -LiteralPath $licenseSrc) {
                Copy-Item -LiteralPath $licenseSrc -Destination (Join-Path $thirdPartyRoot "openslide-bin") -Recurse -Force
            }
            $metaSrc = Join-Path $distInfo.FullName "METADATA"
            if (Test-Path -LiteralPath $metaSrc) {
                Copy-Item -LiteralPath $metaSrc -Destination (Join-Path $thirdPartyRoot "openslide-bin-METADATA.txt") -Force
            }
        }
    }
}
catch {
    Write-Host "Warning: could not collect openslide-bin third-party licenses." -ForegroundColor Yellow
}

$batPath = Join-Path $bundleDir "run-local-viewer.bat"
$batContent = @'
@echo off
setlocal
if "%~1"=="" (
  echo Usage: run-local-viewer.bat ^<SlideRoot^> [Port]
  echo Example: run-local-viewer.bat "D:\WSIs" 8011
  exit /b 1
)
set "SLIDE_ROOT=%~1"
set "PORT=%~2"
if "%PORT%"=="" set "PORT=8011"

"%~dp0LocalWSIViewer.exe" --slide-root "%SLIDE_ROOT%" --port %PORT% --auto-port
'@
[System.IO.File]::WriteAllText($batPath, $batContent, [System.Text.Encoding]::ASCII)

$notesPath = Join-Path $bundleDir "OPEN_SOURCE_NOTICE.txt"
$notesContent = @'
LocalWSIViewer Bundle
=====================

This bundle packages the Local WSI Viewer for Windows workstation usage.

Quick start:
1) Double-click run-local-viewer.bat or run it in cmd/powershell:
   run-local-viewer.bat "D:\your\wsi\folder" 8011
2) Open browser:
   http://127.0.0.1:8011

The repository source and contribution guide are available in the open-source project.
'@
[System.IO.File]::WriteAllText($notesPath, $notesContent, [System.Text.Encoding]::ASCII)

$zipPath = Join-Path $bundleRoot "LocalWSIViewer-windows.zip"
if (-not $SkipZip) {
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $bundleDir "*") -DestinationPath $zipPath -Force
}

Write-Host "Build completed." -ForegroundColor Green
Write-Host "Bundle directory: $bundleDir" -ForegroundColor DarkGray
if (-not $SkipZip) {
    Write-Host "Bundle zip      : $zipPath" -ForegroundColor DarkGray
}
