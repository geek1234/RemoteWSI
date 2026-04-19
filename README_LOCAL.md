# Local WSI Viewer Mode

This repository now includes a local workstation mode optimized for direct WSI viewing:

- Entry point: `app.local_main:app`
- Python launcher: `app.local_launcher:main`
- UI template: `app/templates/local_viewer.html`
- Windows launcher: `scripts/run-local-viewer.ps1`

## Why this mode is faster for local use

1. Reuses `OpenSlide` handles with an LRU pool (`SlidePool`)
2. Reuses `DeepZoomGenerator` instances with an LRU pool (`DZPool`)
3. Removes Redis dependency from the request path
4. Uses short-lived local scan cache with manual reindex endpoint
5. Uses in-memory LRU caches for `meta/thumb/dzi/tile` responses
6. Uses associated-image thumbnail path when available (`thumbnail/macro/label`)
7. Uses thread-pool execution + timeout guards + concurrency caps for blocking operations

## Run on Windows

Install dependencies:

```powershell
py -3 -m pip install fastapi uvicorn pillow openslide-python openslide-bin jinja2 numpy scipy opencv-python-headless
```

Start viewer:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011
```

Or run via Python CLI entrypoint:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
py -3.14 -m app.local_launcher --slide-root "D:\your\wsi\folder" --port 8011 --auto-port
```

Optional package storage directory (recommended when receiving from remote sender):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -PackageRoot "D:\wsi-packages" -Port 8011
```

Use hot-reload only when debugging frontend/backend code:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011 -Reload
```

Open:

`http://127.0.0.1:8011`

## Build Windows distributable bundle

Build a redistributable Windows folder + zip (PyInstaller):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-bundle.ps1 -PythonVersion 3.14
```

Output:

- `dist\windows\LocalWSIViewer\`
- `dist\windows\LocalWSIViewer-windows.zip`

Run packaged app:

```powershell
cd .\dist\windows\LocalWSIViewer
.\run-local-viewer.bat "D:\your\wsi\folder" 8011
```

Online GDDN workflow in UI:

1. Select `Source A` and `Target B` in the bottom-right GDDN panel.
2. Keep `prefer low magnification` enabled for large WSI.
3. Click `Run GDDN(A,B)`.
4. View online outputs directly in the panel:
   - Source Preview
   - Target Preview
   - Normalized (Tiled)
   - Normalized (Mono)
   - Difference Heatmap

## One-command smoke test

Run an automated endpoint check (`health`, `slides`, `meta`, `thumb`, `dzi`):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011 -CheckAllSlides
```

Run a simple UI interaction smoke test (load page, click slide, check metadata, reindex):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
py -3.14 -m pip install playwright
py -3.14 -m playwright install chromium
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-ui.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011 -SearchText "X195937"
```

If a port is occupied, switch to a different one (for example `-Port 18011`).
You can also let the launcher pick the next free port automatically with `-AutoPort`.

Run local performance benchmark:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-perf.ps1 -SlideRoot "D:\your\wsi\folder" -Port 18021 -Iterations 25 -Warmup 3
```

Run package-receiver smoke test (verify package upload/import/decode/download path):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-package-import.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -PackagePath "D:\exports\case_001.tawpkg" -Port 8011
```

Run GDDN color-normalization smoke test (paired A/B slides):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-color-normalization.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 18081
```

Run online color-normalization UI smoke test (select A/B and visualize in browser):

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-color-normalization-ui.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 18087
```

## A/B transfer workflow (TransAnyWhere sender -> WSI Viewer receiver)

1. Start Hospital B receiver (this project), for example:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 8011
```

2. In Hospital A sender (`TransAnyWhere-main`), import WSI, then push selected slide package to:

`http://<HospitalB-IP>:8011`

3. Hospital B opens UI `http://127.0.0.1:8011`, receives the imported package in slide list, and reviews it with pan/zoom.

## Remote server streaming workflow (Ubuntu server -> Windows viewer)

Goal: keep SVS on server, and only stream DZI/tile data to local Windows browser (no full SVS download to local).

### 1) Deploy on Ubuntu server (example uses your server path)

Server path:

`/data3/Eryuan/Transfer/HCCdata/HCC-pic`

Run on server:

```bash
cd /path/to/wsi-viewer-main
chmod +x ./scripts/run-remote-viewer.sh
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libopenslide0

./scripts/run-remote-viewer.sh \
  --slide-root /data3/Eryuan/Transfer/HCCdata/HCC-pic \
  --package-root /data3/Eryuan/Transfer/HCCdata/HCC-packages \
  --host 0.0.0.0 \
  --port 8011
```

If firewall is enabled:

```bash
sudo ufw allow 8011/tcp
```

### 2) Validate from Windows client (streaming test)

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\test-remote-streaming.ps1 -RemoteBaseUrl "http://172.23.157.49:8011"
```

This script checks:

- `GET /health`
- `GET /api/slides`
- `GET /api/meta/{slide_id}`
- `GET /dzi/{slide_id}.dzi`
- sample tile requests `GET /dzi/{slide_id}_files/...`

If these pass, local browser is loading remote tiles on demand rather than pulling full SVS.

### 3) Open remote viewer directly on Windows

Open:

`http://172.23.157.49:8011`

## Optional environment variables

- `WSI_LOCAL_ROOT`
- `WSI_LOCAL_PACKAGE_ROOT`
- `WSI_LOCAL_EXTENSIONS`
- `WSI_LOCAL_SCAN_CACHE_SECONDS`
- `WSI_LOCAL_TILE_SIZE`
- `WSI_LOCAL_TILE_QUALITY`
- `WSI_LOCAL_THUMB_MAX_PX`
- `WSI_LOCAL_THREAD_POOL_SIZE`
- `WSI_LOCAL_SLIDE_POOL_SIZE`
- `WSI_LOCAL_DZ_POOL_SIZE`
- `WSI_LOCAL_MAX_CONCURRENT_THUMBS`
- `WSI_LOCAL_MAX_CONCURRENT_TILES`
- `WSI_LOCAL_THUMB_CACHE_ITEMS`
- `WSI_LOCAL_THUMB_CACHE_MAX_MB`
- `WSI_LOCAL_TILE_CACHE_ITEMS`
- `WSI_LOCAL_TILE_CACHE_MAX_MB`
- `WSI_LOCAL_DZI_CACHE_ITEMS`
- `WSI_LOCAL_DZI_CACHE_TTL_SECONDS`
- `WSI_LOCAL_META_CACHE_ITEMS`
- `WSI_LOCAL_META_CACHE_TTL_SECONDS`
- `WSI_LOCAL_THUMB_PREFER_ASSOCIATED`
- `WSI_LOCAL_JPEG_OPTIMIZE`
- `WSI_LOCAL_JPEG_PROGRESSIVE`

## Local API surface

- `GET /`
- `GET /api/slides?q=&offset=&limit=`
- `POST /api/packages/import` (receive `.tawpkg` / `.zip`, body is binary stream)
- `GET /api/slides/{slide_id}/package` (download stored package for imported package slides)
- `POST /api/color-normalization/run` (GDDN paired stain normalization on source slide using target slide style)
- `GET /api/color-normalization/results/{result_id}/{artifact_name}` (download normalization artifacts)
- `POST /api/reindex`
- `GET /api/meta/{slide_id}`
- `GET /api/thumb/{slide_id}`
- `GET /dzi/{slide_id}.dzi`
- `GET /dzi/{slide_id}_files/{level}/{x}_{y}.jpeg`
- `GET /api/perf`
- `GET /health`

### GDDN normalization request example

```powershell
$body = @{
  sourceSlideId = "SOURCE_ID"
  targetSlideId = "TARGET_ID"
  tau = 0.2
  lam = 5.0
  tileRows = 4
  tileCols = 4
  maxSide = 2048
  preferLowMagnification = $true
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8011/api/color-normalization/run" `
  -ContentType "application/json" `
  -Body $body
```

Notes:

- Supports different source/target image sizes.
- For very large WSI, server automatically prefers lower-magnification pyramid levels when `preferLowMagnification=true`.

## Open-source publication baseline

For open-source release, keep these files in sync:

- `LICENSE`
- `CONTRIBUTING.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`
- `.github/workflows/ci.yml`
- `docs/open_source_release_checklist.md`

Generate source release zip:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\build-source-release.ps1 -DateTag 20260419
```
