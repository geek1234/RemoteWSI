# Contributing Guide

Thank you for contributing to this project.

## Development Setup

1. Create a Python environment (3.13+).
2. Install dependencies:

```bash
pip install -e .
```

3. Run local viewer (Windows example):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011
```

## Testing

Endpoint smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-viewer.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011
```

UI smoke tests:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-local-ui.ps1 -SlideRoot "D:\your\wsi\folder" -Port 8011
powershell -ExecutionPolicy Bypass -File .\scripts\test-color-normalization-ui.ps1 -SlideRoot "D:\your\wsi\folder" -Port 18087
```

## Pull Request Checklist

1. Keep changes focused and scoped to the feature/fix.
2. Add or update tests for user-visible behavior.
3. Update docs when API/UI/CLI behavior changes.
4. Confirm scripts run on Windows PowerShell.

## Coding Notes

- Prefer existing app patterns in `app/local_main.py`.
- Keep WSI operations non-blocking at API level (thread-pool path).
- Preserve compatibility with `.svs`, `.tif/.tiff`, and common pathology image formats.
