# Open Source Release Checklist

## 1. Repository Hygiene

- [ ] Remove local temp files and private datasets
- [ ] Verify no credentials in scripts/config
- [ ] Confirm `.gitignore` excludes build outputs and caches
- [ ] Ensure `LICENSE` is present and matches release intent

## 2. Documentation

- [ ] `README.md` includes project overview and quick start
- [ ] `README_LOCAL.md` includes local mode and test scripts
- [ ] `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md` included
- [ ] Add architecture/app figures for publication use

## 3. Functional Verification

- [ ] `test-local-viewer.ps1` passes
- [ ] `test-local-ui.ps1` passes
- [ ] `test-color-normalization.ps1` passes
- [ ] `test-color-normalization-ui.ps1` passes

## 4. Binary Packaging (Windows)

- [ ] Run `scripts/build-windows-bundle.ps1`
- [ ] Verify `dist/windows/LocalWSIViewer/run-local-viewer.bat`
- [ ] Verify browser opens and serves `http://127.0.0.1:<port>`
- [ ] Attach `LocalWSIViewer-windows.zip` to GitHub Release

## 5. Release Metadata

- [ ] Create tag (`vX.Y.Z`)
- [ ] Publish release notes (features/fixes/known limitations)
- [ ] Attach source zip and optional Windows bundle zip
