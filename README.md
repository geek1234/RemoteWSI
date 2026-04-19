# RemoteWSI

RemoteWSI is an open-source pathology WSI system for:

- Cross-site transfer (Hospital A -> Hospital B)
- Remote streaming review (server-side slide storage, client-side on-demand tiles)
- Online GDDN(A,B) color normalization with interactive visualization

This repository contains the thesis-oriented `v1.0.0` engineering baseline.

![Viewer](assets/fullscreenviewer.png)

## 中文简介

RemoteWSI 面向病理全视野图像（WSI）跨机构协作场景，形成“传输-解码-评阅-标准化”闭环：

1. 医院A可将大尺寸病理图像打包传输至医院B。
2. 医院B可直接在线查看（平移、缩放、导航）而不必强制完整下载原始大文件。
3. 支持在线 GDDN(A,B) 颜色标准化，输出 Source/Target/Tiled/Mono/Heatmap 五类结果。
4. 结果图可点击切换到主视图，信息区与 GDDN 区域支持折叠和高度调节。

## English Summary

RemoteWSI provides an end-to-end workflow for digital pathology collaboration:

- package transfer between institutions
- remote deep-zoom viewing via OpenSeadragon
- online paired color normalization (GDDN A->B style adaptation)
- reviewer-friendly UI for rapid comparative inspection

## Key Features

- WSI format support: `.svs`, `.tif/.tiff`, `.ndpi`, `.mrxs`, `.scn`, plus common image formats
- OpenSlide + DeepZoom tile serving with local cache and resource pooling
- Package receiver API for `.tawpkg` / `.zip`
- Online GDDN normalization API and UI panel
- Remote streaming workflow for Ubuntu server -> Windows client
- Automated smoke tests for API/UI/color-normalization paths
- Windows distributable bundle build script (PyInstaller)
- Open-source baseline docs (`CONTRIBUTING`, `SECURITY`, CI workflow)

## Quick Start (Windows Local Mode)

Install dependencies:

```powershell
py -3 -m pip install fastapi uvicorn pillow openslide-python openslide-bin jinja2 numpy scipy opencv-python-headless
```

Run viewer:

```powershell
cd C:\Users\Admin\Documents\Junlong\Codex\wsi-viewer-main
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 8011
```

Open in browser:

`http://127.0.0.1:8011`

## Online GDDN(A,B) Workflow

1. Select `Source A` and `Target B` in the GDDN panel.
2. Set parameters (`tau`, `lambda`, `tileRows`, `tileCols`, `maxSide`).
3. Keep `prefer low magnification` enabled for large WSI.
4. Click `Run GDDN(A,B)`.
5. Inspect outputs:
   - Source Preview
   - Target Preview
   - Normalized (Tiled)
   - Normalized (Mono)
   - Difference Heatmap

## Smoke Tests

API smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-viewer.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 8011
```

UI smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-ui.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 8011
```

Online color-normalization UI smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-color-normalization-ui.ps1 -SlideRoot "C:\Users\Admin\Documents\WSIs" -Port 18087
```

## Remote Streaming Deployment (Ubuntu -> Windows)

Deploy on Ubuntu server:

```bash
cd /path/to/wsi-viewer-main
chmod +x ./scripts/run-remote-viewer.sh
./scripts/run-remote-viewer.sh \
  --slide-root /data3/Eryuan/Transfer/HCCdata/HCC-pic \
  --package-root /data3/Eryuan/Transfer/HCCdata/HCC-packages \
  --host 0.0.0.0 \
  --port 8011
```

Open from Windows browser:

`http://<server-ip>:8011`

## Build & Release Assets

Build Windows distributable bundle:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows-bundle.ps1 -PythonVersion 3.14
```

Build open-source source zip:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-source-release.ps1 -DateTag 20260419
```

Typical outputs:

- `dist\windows\LocalWSIViewer-windows.zip`
- `dist\open-source\wsi-viewer-main-source-20260419.zip`

## v1.0.0 Release Notes

Bilingual release notes (thesis-oriented):

- `docs/releases/v1.0.0_RELEASE_NOTES.md`

## Repository Docs

- Local mode guide: `README_LOCAL.md`
- Contributing: `CONTRIBUTING.md`
- Security policy: `SECURITY.md`
- Code of conduct: `CODE_OF_CONDUCT.md`
- Release checklist: `docs/open_source_release_checklist.md`

## License

MIT License. See `LICENSE`.
