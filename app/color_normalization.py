from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import openslide
from PIL import Image
from scipy.fft import dctn, idctn


@dataclass
class NormalizationOptions:
    tau: float = 0.2
    lam: float = 5.0
    tile_rows: int = 4
    tile_cols: int = 4
    max_side: int = 2048
    prefer_low_magnification: bool = True


class GDDNNormalizer:
    def __init__(self, tau: float = 0.2, lam: float = 5.0):
        self.tau = float(tau)
        self.lam = float(lam)
        self.target_stats: tuple[list[float], list[float]] | None = None
        self.target_grad_median: list[float] | None = None
        self.source_global_stats: tuple[list[float], list[float]] | None = None
        self.source_grad_median: list[float] | None = None

    @staticmethod
    def _get_lab(img_rgb: np.ndarray) -> np.ndarray:
        img_float = img_rgb.astype(np.float32) / 255.0
        return cv2.cvtColor(img_float, cv2.COLOR_RGB2LAB)

    @staticmethod
    def _to_rgb(img_lab: np.ndarray) -> np.ndarray:
        img_rgb = cv2.cvtColor(img_lab, cv2.COLOR_LAB2RGB)
        return np.clip(img_rgb * 255.0, 0, 255).astype(np.uint8)

    @staticmethod
    def _compute_stats(img_lab: np.ndarray) -> tuple[list[float], list[float]]:
        means = [float(np.mean(img_lab[..., i])) for i in range(3)]
        stds = [float(np.std(img_lab[..., i])) for i in range(3)]
        return means, stds

    @staticmethod
    def _compute_gradients(img_channel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        grads = np.gradient(img_channel)
        return grads[0], grads[1]

    @staticmethod
    def _solve_screened_poisson(f: np.ndarray, lam: float) -> np.ndarray:
        h, w = f.shape
        f_hat = dctn(f, type=2, norm="ortho")

        x = np.arange(h, dtype=np.float64)
        y = np.arange(w, dtype=np.float64)
        cx = 2.0 * np.cos(np.pi * x / max(h, 1))
        cy = 2.0 * np.cos(np.pi * y / max(w, 1))
        lambda_eigen = (cx[:, None] + cy[None, :]) - 4.0

        denom = lambda_eigen - lam
        denom[np.abs(denom) < 1e-10] = 1e-10
        u_hat = f_hat / denom
        return idctn(u_hat, type=2, norm="ortho")

    def fit(self, target_img_rgb: np.ndarray) -> None:
        target_lab = self._get_lab(target_img_rgb)
        self.target_stats = self._compute_stats(target_lab)

        self.target_grad_median = []
        for c in range(3):
            dy, dx = self._compute_gradients(target_lab[..., c])
            mag = np.sqrt(dy * dy + dx * dx)
            self.target_grad_median.append(float(np.median(mag)))

    def precompute_source_stats(self, full_source_img_rgb: np.ndarray) -> None:
        source_lab = self._get_lab(full_source_img_rgb)
        self.source_global_stats = self._compute_stats(source_lab)

        self.source_grad_median = []
        for c in range(3):
            dy, dx = self._compute_gradients(source_lab[..., c])
            mag = np.sqrt(dy * dy + dx * dx)
            self.source_grad_median.append(float(np.median(mag)))

    def transform_tile(self, tile_rgb: np.ndarray) -> np.ndarray:
        if self.target_stats is None or self.target_grad_median is None:
            raise ValueError("run fit(target) first")
        if self.source_global_stats is None or self.source_grad_median is None:
            raise ValueError("run precompute_source_stats(source) first")

        source_lab = self._get_lab(tile_rgb)
        mu_s, sigma_s = self.source_global_stats
        mu_t, sigma_t = self.target_stats

        transfer = np.zeros_like(source_lab)
        for c in range(3):
            safe_sigma = sigma_s[c] if sigma_s[c] > 1e-6 else 1e-6
            transfer[..., c] = (sigma_t[c] / safe_sigma) * (source_lab[..., c] - mu_s[c]) + mu_t[c]

        dy_l, dx_l = self._compute_gradients(source_lab[..., 0])
        grad_mag_sq_l = dy_l * dy_l + dx_l * dx_l
        w = np.exp(-grad_mag_sq_l / max(self.tau * self.tau, 1e-8))

        normalized_lab = np.zeros_like(source_lab)
        for c in range(3):
            grad_is_y, grad_is_x = self._compute_gradients(source_lab[..., c])
            grad_t_y, grad_t_x = self._compute_gradients(transfer[..., c])

            med_is = self.source_grad_median[c] if self.source_grad_median[c] > 1e-6 else 1e-6
            alpha_c = self.target_grad_median[c] / med_is

            g_c_y = (1.0 - w) * alpha_c * grad_is_y + w * grad_t_y
            g_c_x = (1.0 - w) * alpha_c * grad_is_x + w * grad_t_x

            div_g = np.gradient(g_c_y, axis=0) + np.gradient(g_c_x, axis=1)
            rhs = div_g - self.lam * transfer[..., c]
            normalized_lab[..., c] = self._solve_screened_poisson(rhs, self.lam)

        return self._to_rgb(normalized_lab)


def _choose_level(slide: openslide.OpenSlide, max_side: int, prefer_low_magnification: bool) -> int:
    level_dims = list(slide.level_dimensions)
    if not level_dims:
        return 0

    candidates = [i for i, (w, h) in enumerate(level_dims) if max(w, h) <= max_side]
    if candidates:
        return candidates[-1] if prefer_low_magnification else candidates[0]

    if prefer_low_magnification:
        return len(level_dims) - 1

    return min(range(len(level_dims)), key=lambda i: abs(max(level_dims[i]) - max_side))


def _resize_if_needed(img_rgb: np.ndarray, max_side: int) -> tuple[np.ndarray, bool]:
    h, w = img_rgb.shape[:2]
    longest = max(h, w)
    if longest <= max_side or max_side <= 0:
        return img_rgb, False

    scale = max_side / float(longest)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(img_rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, True


def load_wsi_preview(path: Path, max_side: int, prefer_low_magnification: bool) -> tuple[np.ndarray, dict[str, Any]]:
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"slide file does not exist: {path}")

    try:
        slide = openslide.open_slide(str(path))
    except Exception:
        slide = None

    if slide is not None:
        try:
            level_used = _choose_level(slide, max_side=max_side, prefer_low_magnification=prefer_low_magnification)
            level_w, level_h = slide.level_dimensions[level_used]
            region = slide.read_region((0, 0), level_used, (int(level_w), int(level_h))).convert("RGB")
            rgb = np.asarray(region, dtype=np.uint8)
            rgb, resized_after_read = _resize_if_needed(rgb, max_side=max_side)

            info = {
                "backend": "openslide",
                "path": str(path),
                "levelUsed": int(level_used),
                "levelWidth": int(level_w),
                "levelHeight": int(level_h),
                "previewWidth": int(rgb.shape[1]),
                "previewHeight": int(rgb.shape[0]),
                "originalWidth": int(slide.dimensions[0]),
                "originalHeight": int(slide.dimensions[1]),
                "resizedAfterRead": bool(resized_after_read),
            }
            return rgb, info
        finally:
            slide.close()

    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"cannot open image: {path}")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    rgb, resized = _resize_if_needed(rgb, max_side=max_side)

    info = {
        "backend": "opencv",
        "path": str(path),
        "levelUsed": 0,
        "previewWidth": int(rgb.shape[1]),
        "previewHeight": int(rgb.shape[0]),
        "originalWidth": int(bgr.shape[1]),
        "originalHeight": int(bgr.shape[0]),
        "resizedAfterRead": bool(resized),
    }
    return rgb, info


def split_image_variable(image: np.ndarray, rows: int, cols: int) -> tuple[list[np.ndarray], list[tuple[int, int, int, int]], tuple[int, int]]:
    h, w, _ = image.shape
    rows = max(1, min(int(rows), h))
    cols = max(1, min(int(cols), w))

    y_edges = np.linspace(0, h, rows + 1, dtype=np.int32)
    x_edges = np.linspace(0, w, cols + 1, dtype=np.int32)

    tiles: list[np.ndarray] = []
    bounds: list[tuple[int, int, int, int]] = []
    for i in range(rows):
        for j in range(cols):
            y1, y2 = int(y_edges[i]), int(y_edges[i + 1])
            x1, x2 = int(x_edges[j]), int(x_edges[j + 1])
            if y2 <= y1 or x2 <= x1:
                continue
            tiles.append(image[y1:y2, x1:x2])
            bounds.append((y1, y2, x1, x2))

    return tiles, bounds, (rows, cols)


def stitch_image_variable(tiles: list[np.ndarray], bounds: list[tuple[int, int, int, int]], shape_hw: tuple[int, int]) -> np.ndarray:
    h, w = shape_hw
    stitched = np.zeros((h, w, 3), dtype=np.uint8)
    for tile, (y1, y2, x1, x2) in zip(tiles, bounds):
        stitched[y1:y2, x1:x2] = tile
    return stitched


def _save_rgb_png(path: Path, img_rgb: np.ndarray) -> None:
    Image.fromarray(img_rgb).save(path, format="PNG")


def run_pair_color_normalization(
    source_path: Path,
    target_path: Path,
    output_root: Path,
    options: NormalizationOptions,
) -> dict[str, Any]:
    start = time.perf_counter()

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    source_rgb, source_info = load_wsi_preview(
        path=source_path,
        max_side=options.max_side,
        prefer_low_magnification=options.prefer_low_magnification,
    )
    target_rgb, target_info = load_wsi_preview(
        path=target_path,
        max_side=options.max_side,
        prefer_low_magnification=options.prefer_low_magnification,
    )

    normalizer = GDDNNormalizer(tau=options.tau, lam=options.lam)
    normalizer.fit(target_rgb)
    normalizer.precompute_source_stats(source_rgb)

    tiles, bounds, used_grid = split_image_variable(source_rgb, options.tile_rows, options.tile_cols)
    processed_tiles = [normalizer.transform_tile(tile) for tile in tiles]
    result_tiled = stitch_image_variable(processed_tiles, bounds, source_rgb.shape[:2])
    result_mono = normalizer.transform_tile(source_rgb)

    diff = np.abs(result_tiled.astype(np.float32) - result_mono.astype(np.float32))
    diff_map_display = np.clip(np.mean(diff, axis=2) * 10.0, 0, 255).astype(np.uint8)
    diff_heatmap_bgr = cv2.applyColorMap(diff_map_display, cv2.COLORMAP_JET)
    diff_heatmap_rgb = cv2.cvtColor(diff_heatmap_bgr, cv2.COLOR_BGR2RGB)

    seed = f"{source_path}|{target_path}|{time.time_ns()}".encode("utf-8")
    result_id = f"gddn_{int(time.time() * 1000)}_{hashlib.sha1(seed).hexdigest()[:10]}"
    result_dir = output_root / result_id
    result_dir.mkdir(parents=True, exist_ok=True)

    source_file = "source_preview.png"
    target_file = "target_preview.png"
    tiled_file = "normalized_tiled.png"
    mono_file = "normalized_mono.png"
    diff_file = "difference_heatmap.png"
    manifest_file = "manifest.json"

    _save_rgb_png(result_dir / source_file, source_rgb)
    _save_rgb_png(result_dir / target_file, target_rgb)
    _save_rgb_png(result_dir / tiled_file, result_tiled)
    _save_rgb_png(result_dir / mono_file, result_mono)
    _save_rgb_png(result_dir / diff_file, diff_heatmap_rgb)

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    manifest = {
        "ok": True,
        "resultId": result_id,
        "elapsedMs": elapsed_ms,
        "source": source_info,
        "target": target_info,
        "options": {
            "tau": options.tau,
            "lam": options.lam,
            "tileRows": int(options.tile_rows),
            "tileCols": int(options.tile_cols),
            "tileGridUsed": [int(used_grid[0]), int(used_grid[1])],
            "maxSide": int(options.max_side),
            "preferLowMagnification": bool(options.prefer_low_magnification),
        },
        "artifacts": {
            "sourcePreview": source_file,
            "targetPreview": target_file,
            "normalizedTiled": tiled_file,
            "normalizedMono": mono_file,
            "differenceHeatmap": diff_file,
            "manifest": manifest_file,
        },
    }
    (result_dir / manifest_file).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest["resultDir"] = str(result_dir)
    return manifest
