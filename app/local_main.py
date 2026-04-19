from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import math
import os
import shutil
import threading
import time
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openslide
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from openslide.deepzoom import DeepZoomGenerator
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from starlette.templating import Jinja2Templates

from .color_normalization import NormalizationOptions, run_pair_color_normalization

Image.MAX_IMAGE_PIXELS = 500_000_000

log = logging.getLogger("wsi-local-viewer")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
ASSOCIATED_THUMB_PREF = ("thumbnail", "macro", "label")
LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
PACKAGE_EXTENSION = ".tawpkg"
PACKAGE_MANIFEST_NAME = "package_manifest.json"
PACKAGE_RECORD_NAME = "package_record.json"
PACKAGE_DEFAULT_DZI = "slide.dzi"
PACKAGE_DEFAULT_TILES = "slide_files"
PACKAGE_SOURCE_TYPE = "package"
NATIVE_SOURCE_TYPE = "native"


def _stable_id_from_path(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _parse_extensions(value: str | None) -> set[str]:
    if not value:
        return {
            ".svs",
            ".tif",
            ".tiff",
            ".ndpi",
            ".mrxs",
            ".scn",
            ".bif",
            ".vms",
            ".vmu",
            ".svslide",
            ".jpg",
            ".jpeg",
            ".png",
            ".bmp",
        }

    exts: set[str] = set()
    for part in value.split(","):
        p = part.strip().lower()
        if not p:
            continue
        exts.add(p if p.startswith(".") else f".{p}")
    return exts


def _read_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        parsed = int(raw)
        return max(minimum, min(maximum, parsed))
    except ValueError:
        return default


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "y"}


def _default_package_root() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "WSILocalViewer" / "packages"
    return Path.home() / ".wsi_local_viewer" / "packages"


def _sanitize_filename(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        return "incoming.tawpkg"
    for bad in '<>:"/\\|?*':
        cleaned = cleaned.replace(bad, "_")
    return cleaned.strip().strip(".") or "incoming.tawpkg"


def _is_subpath(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_extract_zip(package_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    root_resolved = target_dir.resolve()

    with zipfile.ZipFile(package_path, "r") as archive:
        for member in archive.infolist():
            if not member.filename:
                continue
            resolved = (target_dir / member.filename).resolve()
            if not _is_subpath(resolved, root_resolved):
                raise ValueError(f"Unsafe package entry: {member.filename}")
        archive.extractall(target_dir)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


CPU_COUNT = os.cpu_count() or 8
ROOT_PATH = Path(os.getenv("WSI_LOCAL_ROOT", str(Path.cwd()))).resolve()
PACKAGE_ROOT = Path(os.getenv("WSI_LOCAL_PACKAGE_ROOT", str(_default_package_root()))).resolve()
COLOR_NORM_ROOT = PACKAGE_ROOT / "color_normalization" / "results"
EXTENSIONS = _parse_extensions(os.getenv("WSI_LOCAL_EXTENSIONS"))
SCAN_CACHE_SECONDS = _read_int_env("WSI_LOCAL_SCAN_CACHE_SECONDS", 30, 1, 300)
TILE_SIZE = _read_int_env("WSI_LOCAL_TILE_SIZE", 256, 64, 1024)
TILE_QUALITY = _read_int_env("WSI_LOCAL_TILE_QUALITY", 82, 40, 95)
THUMB_MAX_PX = _read_int_env("WSI_LOCAL_THUMB_MAX_PX", 420, 64, 2048)
MAX_SCAN_ITEMS = _read_int_env("WSI_LOCAL_MAX_SCAN_ITEMS", 20000, 500, 200000)
THREAD_POOL_SIZE = _read_int_env("WSI_LOCAL_THREAD_POOL_SIZE", min(32, max(8, CPU_COUNT * 2)), 2, 64)
SLIDE_POOL_SIZE = _read_int_env("WSI_LOCAL_SLIDE_POOL_SIZE", 24, 4, 128)
DZ_POOL_SIZE = _read_int_env("WSI_LOCAL_DZ_POOL_SIZE", 24, 4, 128)
MAX_CONCURRENT_THUMBS = _read_int_env("WSI_LOCAL_MAX_CONCURRENT_THUMBS", max(2, THREAD_POOL_SIZE // 2), 1, 64)
MAX_CONCURRENT_TILES = _read_int_env("WSI_LOCAL_MAX_CONCURRENT_TILES", min(64, THREAD_POOL_SIZE * 2), 1, 128)
THUMB_CACHE_ITEMS = _read_int_env("WSI_LOCAL_THUMB_CACHE_ITEMS", 256, 0, 20000)
THUMB_CACHE_MAX_MB = _read_int_env("WSI_LOCAL_THUMB_CACHE_MAX_MB", 96, 8, 4096)
TILE_CACHE_ITEMS = _read_int_env("WSI_LOCAL_TILE_CACHE_ITEMS", 4096, 0, 50000)
TILE_CACHE_MAX_MB = _read_int_env("WSI_LOCAL_TILE_CACHE_MAX_MB", 384, 32, 16384)
DZI_CACHE_ITEMS = _read_int_env("WSI_LOCAL_DZI_CACHE_ITEMS", 1024, 0, 10000)
DZI_CACHE_TTL_SECONDS = _read_int_env("WSI_LOCAL_DZI_CACHE_TTL_SECONDS", 3600, 10, 86400)
META_CACHE_ITEMS = _read_int_env("WSI_LOCAL_META_CACHE_ITEMS", 1024, 0, 20000)
META_CACHE_TTL_SECONDS = _read_int_env("WSI_LOCAL_META_CACHE_TTL_SECONDS", 120, 5, 3600)
JPEG_OPTIMIZE = _read_bool_env("WSI_LOCAL_JPEG_OPTIMIZE", False)
JPEG_PROGRESSIVE = _read_bool_env("WSI_LOCAL_JPEG_PROGRESSIVE", False)
THUMB_PREFER_ASSOCIATED = _read_bool_env("WSI_LOCAL_THUMB_PREFER_ASSOCIATED", True)

executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE, thread_name_prefix="wsi-local")


@dataclass
class SlideRecord:
    id: str
    name: str
    relative_path: str
    absolute_path: str
    size: int
    mtime: int
    query_name: str = field(repr=False)
    query_path: str = field(repr=False)


@dataclass
class PackageRecord:
    id: str
    name: str
    relative_path: str
    package_path: str
    extracted_path: str
    dzi_name: str
    tiles_dir_name: str
    size: int
    mtime: int
    width: int
    height: int
    level_count: int | None
    vendor: str | None
    objective_power: str | None
    mpp_x: float | None
    mpp_y: float | None
    file_format: str
    source_type: str = PACKAGE_SOURCE_TYPE
    query_name: str = field(repr=False, default="")
    query_path: str = field(repr=False, default="")


class ColorNormalizationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_slide_id: str = Field(alias="sourceSlideId", min_length=1)
    target_slide_id: str = Field(alias="targetSlideId", min_length=1)
    tau: float = Field(default=0.2, ge=0.01, le=5.0)
    lam: float = Field(default=5.0, ge=0.1, le=50.0)
    tile_rows: int = Field(default=4, alias="tileRows", ge=1, le=128)
    tile_cols: int = Field(default=4, alias="tileCols", ge=1, le=128)
    max_side: int = Field(default=2048, alias="maxSide", ge=256, le=16384)
    prefer_low_magnification: bool = Field(default=True, alias="preferLowMagnification")


class PackageRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self._package_dir = self.root / "uploaded"
        self._incoming_dir = self.root / "incoming"
        self._extract_dir = self.root / "extracted"
        self._lock = threading.Lock()
        self._records: dict[str, PackageRecord] = {}
        self._last_refresh = 0.0
        self._ensure_dirs()

    @property
    def incoming_dir(self) -> Path:
        return self._incoming_dir

    def _ensure_dirs(self) -> None:
        self._package_dir.mkdir(parents=True, exist_ok=True)
        self._incoming_dir.mkdir(parents=True, exist_ok=True)
        self._extract_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _manifest_value(manifest: dict[str, Any], *keys: str, default: Any = "") -> Any:
        for key in keys:
            value = manifest.get(key)
            if value not in (None, ""):
                return value
        return default

    @staticmethod
    def _compute_level_count(width: int, height: int) -> int | None:
        longest = max(width, height)
        if longest <= 0:
            return None
        return int(math.ceil(math.log2(longest)) + 1)

    def _build_record_from_dir(self, slide_id: str, extracted_dir: Path) -> PackageRecord:
        manifest = self._load_json(extracted_dir / PACKAGE_MANIFEST_NAME)
        meta = self._load_json(extracted_dir / PACKAGE_RECORD_NAME)

        package_name = str(meta.get("packageName") or "").strip()
        package_file = (self._package_dir / package_name) if package_name else None
        package_exists = bool(package_file and package_file.exists())

        dzi_name = str(
            self._manifest_value(
                manifest,
                "dziFileName",
                "DziFileName",
                default=PACKAGE_DEFAULT_DZI,
            )
        ).strip() or PACKAGE_DEFAULT_DZI
        tiles_dir_name = str(
            self._manifest_value(
                manifest,
                "tilesDirectoryName",
                "TilesDirectoryName",
                default=PACKAGE_DEFAULT_TILES,
            )
        ).strip() or PACKAGE_DEFAULT_TILES

        dzi_path = (extracted_dir / dzi_name).resolve()
        tiles_dir = (extracted_dir / tiles_dir_name).resolve()
        if not _is_subpath(dzi_path, extracted_dir) or not _is_subpath(tiles_dir, extracted_dir):
            raise ValueError("Unsafe package paths.")
        if not dzi_path.exists() or not dzi_path.is_file():
            raise FileNotFoundError(f"Missing DZI descriptor: {dzi_name}")
        if not tiles_dir.exists() or not tiles_dir.is_dir():
            raise FileNotFoundError(f"Missing tiles directory: {tiles_dir_name}")

        width = _to_int(self._manifest_value(manifest, "width", "Width", default=0))
        height = _to_int(self._manifest_value(manifest, "height", "Height", default=0))
        level_count = self._compute_level_count(width, height)

        mpp_x = _to_float_or_none(self._manifest_value(manifest, "mppX", "MppX", default=None))
        mpp_y = _to_float_or_none(self._manifest_value(manifest, "mppY", "MppY", default=None))
        objective_power = self._manifest_value(manifest, "objectivePower", "ObjectivePower", default=None)
        vendor = self._manifest_value(manifest, "vendor", "Vendor", default=None)
        file_format = str(self._manifest_value(manifest, "fileFormat", "FileFormat", default="package"))

        slide_name = str(
            self._manifest_value(
                manifest,
                "fileName",
                "FileName",
                default=(package_name or f"{slide_id}.slide"),
            )
        )
        if package_exists and package_file is not None:
            stat = package_file.stat()
            size = stat.st_size
            mtime = int(stat.st_mtime)
            package_path = str(package_file)
        else:
            size = _to_int(self._manifest_value(manifest, "originalSize", "OriginalSize", default=0))
            mtime = int(extracted_dir.stat().st_mtime)
            package_path = ""

        relative = f"packages/{package_name}" if package_name else f"packages/{slide_id}{PACKAGE_EXTENSION}"

        return PackageRecord(
            id=slide_id,
            name=slide_name,
            relative_path=relative,
            package_path=package_path,
            extracted_path=str(extracted_dir),
            dzi_name=dzi_name,
            tiles_dir_name=tiles_dir_name,
            size=size,
            mtime=mtime,
            width=width,
            height=height,
            level_count=level_count,
            vendor=vendor,
            objective_power=objective_power if objective_power not in ("", None) else None,
            mpp_x=mpp_x,
            mpp_y=mpp_y,
            file_format=file_format,
            source_type=PACKAGE_SOURCE_TYPE,
            query_name=slide_name.lower(),
            query_path=relative.lower(),
        )

    def refresh(self) -> None:
        self._ensure_dirs()
        records: dict[str, PackageRecord] = {}
        for child in self._extract_dir.iterdir():
            if not child.is_dir():
                continue
            slide_id = child.name
            try:
                record = self._build_record_from_dir(slide_id, child)
                records[slide_id] = record
            except Exception as exc:
                log.warning("Skip invalid package '%s': %s", child, exc)
                continue

        with self._lock:
            self._records = records
            self._last_refresh = time.time()

    def list(self, query: str = "") -> list[PackageRecord]:
        self.refresh()
        q = query.lower().strip()
        with self._lock:
            items = list(self._records.values())
        if q:
            items = [x for x in items if q in x.query_name or q in x.query_path]
        items.sort(key=lambda r: r.query_path)
        return items

    def get(self, slide_id: str) -> PackageRecord | None:
        with self._lock:
            cached = self._records.get(slide_id)
        if cached is not None:
            return cached
        self.refresh()
        with self._lock:
            return self._records.get(slide_id)

    def _write_record_meta(self, extracted_dir: Path, package_name: str) -> None:
        meta = {
            "packageName": package_name,
            "importedAt": int(time.time()),
        }
        (extracted_dir / PACKAGE_RECORD_NAME).write_text(
            json.dumps(meta, ensure_ascii=True),
            encoding="utf-8",
        )

    def import_package(self, source_package: Path, preferred_name: str | None = None) -> PackageRecord:
        if not source_package.exists() or not source_package.is_file():
            raise FileNotFoundError("Package file does not exist.")

        self._ensure_dirs()
        seed = f"{source_package}:{time.time_ns()}".encode("utf-8")
        slide_id = f"pkg_{hashlib.sha1(seed).hexdigest()[:20]}"

        package_name = _sanitize_filename(preferred_name or source_package.name)
        if not package_name.lower().endswith((PACKAGE_EXTENSION, ".zip")):
            package_name = f"{package_name}{PACKAGE_EXTENSION}"

        stored_package = self._package_dir / f"{slide_id}_{package_name}"
        extracted_dir = self._extract_dir / slide_id

        if extracted_dir.exists():
            shutil.rmtree(extracted_dir, ignore_errors=True)

        try:
            shutil.copyfile(source_package, stored_package)
            _safe_extract_zip(stored_package, extracted_dir)
            self._write_record_meta(extracted_dir, stored_package.name)
            record = self._build_record_from_dir(slide_id, extracted_dir)
        except Exception:
            try:
                if stored_package.exists():
                    stored_package.unlink()
            except OSError:
                pass
            shutil.rmtree(extracted_dir, ignore_errors=True)
            raise

        with self._lock:
            self._records[slide_id] = record
        return record

    def resolve_dzi_path(self, slide_id: str) -> Path:
        record = self.get(slide_id)
        if record is None:
            raise FileNotFoundError(slide_id)
        path = (Path(record.extracted_path) / record.dzi_name).resolve()
        if not path.exists() or not _is_subpath(path, Path(record.extracted_path)):
            raise FileNotFoundError("Missing DZI file.")
        return path

    def resolve_tile_path(self, slide_id: str, level: int, x: int, y: int) -> Path:
        record = self.get(slide_id)
        if record is None:
            raise FileNotFoundError(slide_id)

        tile_root = (Path(record.extracted_path) / record.tiles_dir_name).resolve()
        if not tile_root.exists() or not tile_root.is_dir() or not _is_subpath(tile_root, Path(record.extracted_path)):
            raise FileNotFoundError("Missing tiles.")

        for ext in (".jpeg", ".jpg", ".png"):
            candidate = (tile_root / str(level) / f"{x}_{y}{ext}").resolve()
            if _is_subpath(candidate, tile_root) and candidate.exists() and candidate.is_file():
                return candidate
        raise FileNotFoundError("Tile not found.")

    def resolve_thumb_tile(self, slide_id: str) -> Path:
        record = self.get(slide_id)
        if record is None:
            raise FileNotFoundError(slide_id)

        tile_root = (Path(record.extracted_path) / record.tiles_dir_name).resolve()
        if not tile_root.exists() or not tile_root.is_dir():
            raise FileNotFoundError("Missing tiles.")

        for ext in (".jpeg", ".jpg", ".png"):
            preferred = tile_root / "0" / f"0_0{ext}"
            if preferred.exists():
                return preferred

        level_dirs = [d for d in tile_root.iterdir() if d.is_dir() and d.name.isdigit()]
        level_dirs.sort(key=lambda d: int(d.name))
        for level_dir in level_dirs:
            files = sorted(
                [
                    f
                    for f in level_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in {".jpeg", ".jpg", ".png"}
                ]
            )
            if files:
                return files[0]

        raise FileNotFoundError("No thumbnail tile found.")

    def resolve_package_file(self, slide_id: str) -> Path:
        record = self.get(slide_id)
        if record is None:
            raise FileNotFoundError(slide_id)
        if not record.package_path:
            raise FileNotFoundError("Package file unavailable.")
        package_file = Path(record.package_path).resolve()
        if not package_file.exists() or not package_file.is_file():
            raise FileNotFoundError("Package file unavailable.")
        return package_file

    def stats(self) -> dict[str, Any]:
        with self._lock:
            count = len(self._records)
            last_refresh = self._last_refresh
        return {
            "root": str(self.root),
            "package_count": count,
            "last_refresh_ts": last_refresh,
        }


class SlideRegistry:
    def __init__(self, root: Path, extensions: set[str], max_items: int, cache_seconds: int):
        self.root = root.resolve()
        self.extensions = {x.lower() for x in extensions}
        self.max_items = max_items
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._records: list[SlideRecord] = []
        self._id_to_path: dict[str, Path] = {}
        self._last_scan = 0.0

    def _scan(self) -> None:
        records: list[SlideRecord] = []
        id_map: dict[str, Path] = {}

        stack = [self.root]
        while stack and len(records) < self.max_items:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue

                            file_path = Path(entry.path)
                            if file_path.suffix.lower() not in self.extensions:
                                continue

                            stat = entry.stat(follow_symlinks=False)
                            slide_id = _stable_id_from_path(file_path)
                            rel = str(file_path.relative_to(self.root))
                            record = SlideRecord(
                                id=slide_id,
                                name=file_path.name,
                                relative_path=rel.replace("\\", "/"),
                                absolute_path=str(file_path),
                                size=stat.st_size,
                                mtime=int(stat.st_mtime),
                                query_name=file_path.name.lower(),
                                query_path=rel.replace("\\", "/").lower(),
                            )
                            records.append(record)
                            id_map[slide_id] = file_path
                        except OSError:
                            continue
            except OSError:
                continue

        records.sort(key=lambda r: r.query_path)
        with self._lock:
            self._records = records
            self._id_to_path = id_map
            self._last_scan = time.time()

    def refresh_if_needed(self, force: bool = False) -> None:
        with self._lock:
            should_scan = force or (time.time() - self._last_scan >= self.cache_seconds)
        if should_scan:
            self._scan()

    def list(self, query: str = "", offset: int = 0, limit: int = 200) -> tuple[list[SlideRecord], int]:
        self.refresh_if_needed()
        q = query.lower().strip()
        with self._lock:
            records = self._records
            if q:
                records = [r for r in records if q in r.query_name or q in r.query_path]
            total = len(records)
            return records[offset: offset + limit], total

    def resolve(self, slide_id: str) -> Path:
        self.refresh_if_needed()
        with self._lock:
            path = self._id_to_path.get(slide_id)
        if path is None or not path.exists():
            self.refresh_if_needed(force=True)
            with self._lock:
                path = self._id_to_path.get(slide_id)
            if path is None or not path.exists():
                raise FileNotFoundError(slide_id)
        return path

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            age = max(0.0, now - self._last_scan) if self._last_scan > 0 else None
            return {
                "root": str(self.root),
                "slide_count": len(self._records),
                "last_scan_ts": self._last_scan,
                "scan_age_seconds": age,
                "max_scan_items": self.max_items,
            }


class SlidePool:
    def __init__(self, max_handles: int = 24):
        self._lock = threading.Lock()
        self._max = max_handles
        self._handles: OrderedDict[str, openslide.OpenSlide] = OrderedDict()

    def get(self, path: Path) -> openslide.OpenSlide:
        key = str(path)
        with self._lock:
            handle = self._handles.get(key)
            if handle is not None:
                self._handles.move_to_end(key)
                return handle

        opened = openslide.open_slide(key)
        with self._lock:
            existing = self._handles.get(key)
            if existing is not None:
                opened.close()
                self._handles.move_to_end(key)
                return existing
            self._handles[key] = opened
            self._handles.move_to_end(key)
            while len(self._handles) > self._max:
                _, old = self._handles.popitem(last=False)
                try:
                    old.close()
                except Exception:
                    pass
            return opened

    def close_all(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                try:
                    handle.close()
                except Exception:
                    pass
            self._handles.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"max_handles": self._max, "open_handles": len(self._handles)}


class DZPool:
    def __init__(self, slide_pool: SlidePool, tile_size: int, max_items: int = 24):
        self.slide_pool = slide_pool
        self.tile_size = tile_size
        self._max = max_items
        self._lock = threading.Lock()
        self._pool: OrderedDict[str, DeepZoomGenerator] = OrderedDict()

    def get(self, path: Path):
        key = str(path)
        with self._lock:
            dz = self._pool.get(key)
            if dz is not None:
                self._pool.move_to_end(key)
                return dz

        slide = self.slide_pool.get(path)
        dz = DeepZoomGenerator(
            slide,
            tile_size=self.tile_size,
            overlap=0,
            limit_bounds=True,
        )

        with self._lock:
            existing = self._pool.get(key)
            if existing is not None:
                self._pool.move_to_end(key)
                return existing
            self._pool[key] = dz
            self._pool.move_to_end(key)
            while len(self._pool) > self._max:
                self._pool.popitem(last=False)
            return dz

    def clear(self) -> None:
        with self._lock:
            self._pool.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"max_items": self._max, "active_items": len(self._pool)}


class ByteLRUCache:
    def __init__(self, name: str, max_items: int, max_bytes: int):
        self.name = name
        self.max_items = max_items
        self.max_bytes = max_bytes
        self.enabled = max_items > 0 and max_bytes > 0
        self._lock = threading.Lock()
        self._items: OrderedDict[str, bytes] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self._misses += 1
                return None
            self._hits += 1
            self._items.move_to_end(key)
            return value

    def set(self, key: str, value: bytes) -> None:
        if not self.enabled:
            return
        payload_size = len(value)
        if payload_size > self.max_bytes:
            return
        with self._lock:
            existing = self._items.pop(key, None)
            if existing is not None:
                self._size_bytes -= len(existing)
            self._items[key] = value
            self._items.move_to_end(key)
            self._size_bytes += payload_size
            while len(self._items) > self.max_items or self._size_bytes > self.max_bytes:
                _, old = self._items.popitem(last=False)
                self._size_bytes -= len(old)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._size_bytes = 0
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total else None
            return {
                "name": self.name,
                "enabled": self.enabled,
                "items": len(self._items),
                "sizeBytes": self._size_bytes,
                "maxItems": self.max_items,
                "maxBytes": self.max_bytes,
                "hits": self._hits,
                "misses": self._misses,
                "hitRate": hit_rate,
            }


class TimedValueCache:
    def __init__(self, name: str, max_items: int, ttl_seconds: int):
        self.name = name
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self.enabled = max_items > 0 and ttl_seconds > 0
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        now = time.time()
        with self._lock:
            value = self._items.get(key)
            if value is None:
                self._misses += 1
                return None
            expire_ts, payload = value
            if expire_ts <= now:
                self._items.pop(key, None)
                self._misses += 1
                return None
            self._hits += 1
            self._items.move_to_end(key)
            return payload

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        expires_at = time.time() + self.ttl_seconds
        with self._lock:
            self._items[key] = (expires_at, value)
            self._items.move_to_end(key)
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._hits = 0
            self._misses = 0

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            stale_keys = [k for k, (ts, _) in self._items.items() if ts <= now]
            for key in stale_keys:
                self._items.pop(key, None)
            total = self._hits + self._misses
            hit_rate = (self._hits / total) if total else None
            return {
                "name": self.name,
                "enabled": self.enabled,
                "items": len(self._items),
                "maxItems": self.max_items,
                "ttlSeconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "hitRate": hit_rate,
            }


registry = SlideRegistry(
    root=ROOT_PATH,
    extensions=EXTENSIONS,
    max_items=MAX_SCAN_ITEMS,
    cache_seconds=SCAN_CACHE_SECONDS,
)
package_registry = PackageRegistry(root=PACKAGE_ROOT)
slide_pool = SlidePool(max_handles=SLIDE_POOL_SIZE)
dz_pool = DZPool(slide_pool=slide_pool, tile_size=TILE_SIZE, max_items=DZ_POOL_SIZE)
thumb_cache = ByteLRUCache("thumb", THUMB_CACHE_ITEMS, THUMB_CACHE_MAX_MB * 1024 * 1024)
tile_cache = ByteLRUCache("tile", TILE_CACHE_ITEMS, TILE_CACHE_MAX_MB * 1024 * 1024)
dzi_cache = TimedValueCache("dzi", DZI_CACHE_ITEMS, DZI_CACHE_TTL_SECONDS)
meta_cache = TimedValueCache("meta", META_CACHE_ITEMS, META_CACHE_TTL_SECONDS)
thumb_semaphore = asyncio.Semaphore(MAX_CONCURRENT_THUMBS)
tile_semaphore = asyncio.Semaphore(MAX_CONCURRENT_TILES)

app = FastAPI(title="Local WSI Viewer", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _file_signature(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return "missing"


def _etag_for_file(path: Path, suffix: str) -> str:
    raw = f"{path}|{_file_signature(path)}|{suffix}"
    return f'W/"{hashlib.sha1(raw.encode()).hexdigest()}"'


def _serialize_record(record: SlideRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "relativePath": record.relative_path,
        "absolutePath": record.absolute_path,
        "size": record.size,
        "mtime": record.mtime,
        "sourceType": NATIVE_SOURCE_TYPE,
    }


def _serialize_package_record(record: PackageRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "relativePath": record.relative_path,
        "absolutePath": record.package_path or record.extracted_path,
        "size": record.size,
        "mtime": record.mtime,
        "sourceType": PACKAGE_SOURCE_TYPE,
    }


def _to_float_or_none(value: Any) -> float | None:
    if value in (None, "", "0"):
        return None
    try:
        parsed = float(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return None


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    save_kwargs: dict[str, Any] = {"format": "JPEG", "quality": quality}
    if JPEG_OPTIMIZE:
        save_kwargs["optimize"] = True
    if JPEG_PROGRESSIVE:
        save_kwargs["progressive"] = True
    image.save(out, **save_kwargs)
    return out.getvalue()


def _build_thumbnail(slide: openslide.OpenSlide) -> bytes:
    if THUMB_PREFER_ASSOCIATED:
        for key in ASSOCIATED_THUMB_PREF:
            if key in slide.associated_images:
                img = slide.associated_images[key].copy()
                img.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX), LANCZOS)
                if img.mode == "RGBA":
                    img = img.convert("RGB")
                return _encode_jpeg(img, TILE_QUALITY)

    thumb = slide.get_thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
    if thumb.mode == "RGBA":
        thumb = thumb.convert("RGB")
    return _encode_jpeg(thumb, TILE_QUALITY)


def _build_package_thumbnail(tile_path: Path) -> bytes:
    with Image.open(tile_path) as image:
        if image.mode == "RGBA":
            image = image.convert("RGB")
        image.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX), LANCZOS)
        return _encode_jpeg(image, TILE_QUALITY)


def _cache_stats() -> dict[str, Any]:
    return {
        "thumbCache": thumb_cache.stats(),
        "tileCache": tile_cache.stats(),
        "dziCache": dzi_cache.stats(),
        "metaCache": meta_cache.stats(),
    }


def _color_norm_stats() -> dict[str, Any]:
    root = COLOR_NORM_ROOT
    if not root.exists() or not root.is_dir():
        return {"root": str(root), "result_count": 0}
    count = sum(1 for p in root.iterdir() if p.is_dir())
    return {"root": str(root), "result_count": count}


def _resolve_native_slide_path(slide_id: str) -> Path:
    package_record = package_registry.get(slide_id)
    if package_record is not None:
        raise ValueError("Package slides are not supported for source/target in GDDN normalization.")
    return registry.resolve(slide_id)


async def _run_blocking(func, *args, timeout: float = 15.0):
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(executor, func, *args)
    return await asyncio.wait_for(future, timeout=timeout)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="local_viewer.html",
        context={"root": str(ROOT_PATH), "package_root": str(PACKAGE_ROOT)},
    )


@app.get("/api/slides")
async def api_slides(
    q: str = Query(default=""),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
):
    try:
        native_items, _ = await _run_blocking(registry.list, q, 0, MAX_SCAN_ITEMS, timeout=30.0)
        package_items = await _run_blocking(package_registry.list, q, timeout=30.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Slide scan timed out.")

    merged = [_serialize_record(x) for x in native_items]
    merged.extend(_serialize_package_record(x) for x in package_items)
    merged.sort(key=lambda item: item.get("mtime", 0), reverse=True)

    total = len(merged)
    page_items = merged[offset: offset + limit]
    return {
        "items": page_items,
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@app.post("/api/packages/import")
async def api_packages_import(request: Request):
    header_name = request.headers.get("X-Package-Name", "")
    incoming_name = _sanitize_filename(header_name or f"incoming_{int(time.time())}{PACKAGE_EXTENSION}")
    if not incoming_name.lower().endswith((PACKAGE_EXTENSION, ".zip")):
        incoming_name = f"{incoming_name}{PACKAGE_EXTENSION}"

    incoming_path = package_registry.incoming_dir / f"{int(time.time() * 1000)}_{incoming_name}"
    bytes_written = 0

    try:
        with incoming_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                bytes_written += len(chunk)
                handle.write(chunk)

        if bytes_written <= 0:
            raise HTTPException(status_code=400, detail="Package body is empty.")

        try:
            record = await _run_blocking(package_registry.import_package, incoming_path, incoming_name, timeout=600.0)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid package file.")
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        return {
            "ok": True,
            "slideId": record.id,
            "fileName": record.name,
            "sourceType": record.source_type,
        }
    finally:
        try:
            if incoming_path.exists():
                incoming_path.unlink()
        except OSError:
            pass


@app.get("/api/slides/{slide_id}/package")
async def api_slide_package(slide_id: str, request: Request):
    try:
        package_path = await _run_blocking(package_registry.resolve_package_file, slide_id, timeout=15.0)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Package file not found.")

    etag = _etag_for_file(package_path, "package")
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-store"})

    headers = {"ETag": etag, "Cache-Control": "no-store"}
    return FileResponse(
        path=package_path,
        filename=package_path.name,
        media_type="application/octet-stream",
        headers=headers,
    )


@app.post("/api/color-normalization/run")
async def api_color_normalization_run(payload: ColorNormalizationRequest):
    try:
        source_path = await _run_blocking(_resolve_native_slide_path, payload.source_slide_id, timeout=15.0)
        target_path = await _run_blocking(_resolve_native_slide_path, payload.target_slide_id, timeout=15.0)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Source or target slide not found.")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    options = NormalizationOptions(
        tau=payload.tau,
        lam=payload.lam,
        tile_rows=payload.tile_rows,
        tile_cols=payload.tile_cols,
        max_side=payload.max_side,
        prefer_low_magnification=payload.prefer_low_magnification,
    )

    timeout_seconds = max(180.0, min(3600.0, 240.0 + (payload.max_side / 2048.0) * 240.0))
    try:
        result = await _run_blocking(
            run_pair_color_normalization,
            source_path,
            target_path,
            COLOR_NORM_ROOT,
            options,
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Color normalization timed out.")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Color normalization failed: {exc}")

    result_id = result.get("resultId", "")
    artifacts = result.get("artifacts", {})
    artifact_urls = {
        key: f"/api/color-normalization/results/{result_id}/{file_name}"
        for key, file_name in artifacts.items()
    }

    return {
        "ok": True,
        "resultId": result_id,
        "elapsedMs": result.get("elapsedMs"),
        "source": result.get("source"),
        "target": result.get("target"),
        "options": result.get("options"),
        "artifacts": artifact_urls,
    }


@app.get("/api/color-normalization/results/{result_id}/{artifact_name}")
async def api_color_normalization_result_file(result_id: str, artifact_name: str):
    root = COLOR_NORM_ROOT.resolve()
    result_dir = (COLOR_NORM_ROOT / result_id).resolve()
    if not _is_subpath(result_dir, root):
        raise HTTPException(status_code=403, detail="Forbidden result path.")

    artifact_path = (result_dir / artifact_name).resolve()
    if not _is_subpath(artifact_path, result_dir):
        raise HTTPException(status_code=403, detail="Forbidden artifact path.")
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found.")

    suffix = artifact_path.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".json": "application/json; charset=utf-8",
    }.get(suffix, "application/octet-stream")

    return FileResponse(path=artifact_path, media_type=media_type, filename=artifact_path.name)


@app.post("/api/reindex")
async def api_reindex():
    await _run_blocking(registry.refresh_if_needed, True, timeout=120.0)
    await _run_blocking(package_registry.refresh, timeout=60.0)
    dz_pool.clear()
    thumb_cache.clear()
    tile_cache.clear()
    dzi_cache.clear()
    meta_cache.clear()
    return {"ok": True, "stats": {"native": registry.stats(), "packages": package_registry.stats()}}


@app.get("/api/meta/{slide_id}")
async def api_meta(slide_id: str):
    package_record = await _run_blocking(package_registry.get, slide_id, timeout=15.0)
    if package_record is not None:
        signature = f"{package_record.id}:{package_record.size}:{package_record.mtime}"
        cache_key = f"pkg:{slide_id}:{signature}"
        cached_meta = meta_cache.get(cache_key)
        if cached_meta is not None:
            return cached_meta

        meta = {
            "id": package_record.id,
            "name": package_record.name,
            "path": package_record.package_path or package_record.extracted_path,
            "width": package_record.width,
            "height": package_record.height,
            "levelCount": package_record.level_count,
            "vendor": package_record.vendor,
            "objectivePower": package_record.objective_power,
            "mppX": package_record.mpp_x,
            "mppY": package_record.mpp_y,
            "fileSize": package_record.size,
            "mtime": package_record.mtime,
            "sourceType": package_record.source_type,
            "format": package_record.file_format,
        }
        meta_cache.set(cache_key, meta)
        return meta

    try:
        path = registry.resolve(slide_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Slide not found.")

    signature = _file_signature(path)
    cache_key = f"{slide_id}:{signature}"
    cached_meta = meta_cache.get(cache_key)
    if cached_meta is not None:
        return cached_meta

    def _read_meta() -> dict[str, Any]:
        slide = slide_pool.get(path)
        mpp_x = _to_float_or_none(slide.properties.get(openslide.PROPERTY_NAME_MPP_X))
        mpp_y = _to_float_or_none(slide.properties.get(openslide.PROPERTY_NAME_MPP_Y))
        return {
            "id": slide_id,
            "name": path.name,
            "path": str(path),
            "width": slide.dimensions[0],
            "height": slide.dimensions[1],
            "levelCount": slide.level_count,
            "vendor": slide.properties.get(openslide.PROPERTY_NAME_VENDOR),
            "objectivePower": slide.properties.get(openslide.PROPERTY_NAME_OBJECTIVE_POWER),
            "mppX": mpp_x,
            "mppY": mpp_y,
            "fileSize": path.stat().st_size,
            "mtime": int(path.stat().st_mtime),
            "sourceType": NATIVE_SOURCE_TYPE,
            "format": path.suffix.lower().lstrip("."),
        }

    try:
        meta = await _run_blocking(_read_meta, timeout=15.0)
        meta_cache.set(cache_key, meta)
        return meta
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Metadata read timed out.")


@app.get("/api/thumb/{slide_id}")
async def api_thumb(slide_id: str, request: Request):
    package_record = await _run_blocking(package_registry.get, slide_id, timeout=15.0)
    if package_record is not None:
        signature = f"{package_record.id}:{package_record.size}:{package_record.mtime}"
        etag_raw = f"pkg-thumb|{signature}|{THUMB_MAX_PX}|{TILE_QUALITY}|{int(JPEG_OPTIMIZE)}"
        etag = f'W/"{hashlib.sha1(etag_raw.encode("utf-8")).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

        cache_key = f"pkg-thumb:{signature}:{THUMB_MAX_PX}:{TILE_QUALITY}:{int(JPEG_OPTIMIZE)}"
        cached = thumb_cache.get(cache_key)
        if cached is not None:
            return Response(content=cached, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

        async with thumb_semaphore:
            second = thumb_cache.get(cache_key)
            if second is not None:
                return Response(content=second, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

            def _make_pkg_thumb() -> bytes:
                tile_path = package_registry.resolve_thumb_tile(slide_id)
                return _build_package_thumbnail(tile_path)

            try:
                content = await _run_blocking(_make_pkg_thumb, timeout=20.0)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Package thumbnail generation timed out.")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="Package thumbnail tile not found.")

            thumb_cache.set(cache_key, content)
            return Response(content=content, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

    try:
        path = registry.resolve(slide_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Slide not found.")

    signature = _file_signature(path)
    etag = _etag_for_file(path, f"thumb:{THUMB_MAX_PX}:{TILE_QUALITY}:{int(JPEG_OPTIMIZE)}:{int(THUMB_PREFER_ASSOCIATED)}")
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

    cache_key = f"{slide_id}:{signature}:{THUMB_MAX_PX}:{TILE_QUALITY}:{int(JPEG_OPTIMIZE)}:{int(THUMB_PREFER_ASSOCIATED)}"
    cached = thumb_cache.get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

    async with thumb_semaphore:
        second = thumb_cache.get(cache_key)
        if second is not None:
            return Response(content=second, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})

        def _make_thumb() -> bytes:
            slide = slide_pool.get(path)
            return _build_thumbnail(slide)

        try:
            content = await _run_blocking(_make_thumb, timeout=15.0)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Thumbnail generation timed out.")

        thumb_cache.set(cache_key, content)
        return Response(content=content, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=300"})


@app.get("/dzi/{slide_id}.dzi")
async def dzi_xml(slide_id: str, request: Request):
    package_record = await _run_blocking(package_registry.get, slide_id, timeout=15.0)
    if package_record is not None:
        try:
            dzi_path = await _run_blocking(package_registry.resolve_dzi_path, slide_id, timeout=15.0)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Package DZI not found.")

        etag = _etag_for_file(dzi_path, f"pkg-dzi:{package_record.id}")
        if request.headers.get("If-None-Match") == etag:
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

        cache_key = f"pkg-dzi:{package_record.id}:{package_record.size}:{package_record.mtime}"
        cached_xml = dzi_cache.get(cache_key)
        if cached_xml is not None:
            return Response(content=cached_xml, media_type="application/xml", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

        def _read_pkg_dzi() -> str:
            return dzi_path.read_text(encoding="utf-8")

        try:
            xml = await _run_blocking(_read_pkg_dzi, timeout=10.0)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Package DZI read timed out.")

        dzi_cache.set(cache_key, xml)
        return Response(
            content=xml,
            media_type="application/xml",
            headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
        )

    try:
        path = registry.resolve(slide_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Slide not found.")

    signature = _file_signature(path)
    etag = _etag_for_file(path, f"dzi:{TILE_SIZE}")
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

    cache_key = f"{slide_id}:{signature}:{TILE_SIZE}"
    cached_xml = dzi_cache.get(cache_key)
    if cached_xml is not None:
        return Response(content=cached_xml, media_type="application/xml", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

    def _render_dzi() -> str:
        dz = dz_pool.get(path)
        return dz.get_dzi("jpeg")

    try:
        xml = await _run_blocking(_render_dzi, timeout=10.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="DZI generation timed out.")

    dzi_cache.set(cache_key, xml)
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
    )


@app.get("/dzi/{slide_id}_files/{level}/{x}_{y}.jpeg")
async def dzi_tile(slide_id: str, level: int, x: int, y: int, request: Request):
    if x < 0 or y < 0:
        raise HTTPException(status_code=404, detail="Tile not found.")

    package_record = await _run_blocking(package_registry.get, slide_id, timeout=15.0)
    if package_record is not None:
        etag_raw = f"pkg-tile|{package_record.id}|{package_record.size}|{package_record.mtime}|{level}|{x}|{y}"
        etag = f'W/"{hashlib.sha1(etag_raw.encode("utf-8")).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
            )

        cache_key = f"pkg-tile:{package_record.id}:{package_record.size}:{package_record.mtime}:{level}:{x}:{y}"
        cached = tile_cache.get(cache_key)
        if cached is not None:
            return Response(content=cached, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

        async with tile_semaphore:
            second = tile_cache.get(cache_key)
            if second is not None:
                return Response(content=second, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

            def _read_pkg_tile() -> bytes:
                tile_path = package_registry.resolve_tile_path(slide_id, level, x, y)
                return tile_path.read_bytes()

            try:
                content = await _run_blocking(_read_pkg_tile, timeout=10.0)
            except asyncio.TimeoutError:
                raise HTTPException(status_code=504, detail="Package tile read timed out.")
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail="Tile not found.")

            tile_cache.set(cache_key, content)
            return Response(content=content, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

    try:
        path = registry.resolve(slide_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Slide not found.")

    signature = _file_signature(path)
    etag = _etag_for_file(path, f"tile:{level}:{x}:{y}:{TILE_SIZE}:{TILE_QUALITY}:{int(JPEG_OPTIMIZE)}")
    if request.headers.get("If-None-Match") == etag:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "public, max-age=3600"},
        )

    cache_key = f"{slide_id}:{signature}:{level}:{x}:{y}:{TILE_SIZE}:{TILE_QUALITY}:{int(JPEG_OPTIMIZE)}"
    cached = tile_cache.get(cache_key)
    if cached is not None:
        return Response(content=cached, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

    async with tile_semaphore:
        second = tile_cache.get(cache_key)
        if second is not None:
            return Response(content=second, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})

        def _read_tile() -> bytes:
            dz = dz_pool.get(path)
            if level < 0 or level >= dz.level_count:
                raise HTTPException(status_code=404, detail="Invalid tile level.")
            try:
                tile = dz.get_tile(level, (x, y))
            except Exception:
                raise HTTPException(status_code=404, detail="Tile not found.")
            if tile.mode == "RGBA":
                tile = tile.convert("RGB")
            return _encode_jpeg(tile, TILE_QUALITY)

        try:
            content = await _run_blocking(_read_tile, timeout=10.0)
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Tile read timed out.")

        tile_cache.set(cache_key, content)
        return Response(content=content, media_type="image/jpeg", headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})


@app.api_route("/logo", methods=["GET", "HEAD"])
async def logo(request: Request):
    for name, ctype in (("logo.png", "image/png"), ("logo.svg", "image/svg+xml")):
        p = STATIC_DIR / name
        if not p.exists():
            continue
        etag = _etag_for_file(p, f"logo:{name}")
        headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
        if request.method == "HEAD":
            return Response(status_code=200, media_type=ctype, headers=headers)
        return Response(content=p.read_bytes(), media_type=ctype, headers=headers)
    raise HTTPException(status_code=404, detail="Logo not found.")


@app.get("/api/perf")
async def api_perf():
    return {
        "status": "ok",
        "runtime": {
            "threadPoolSize": THREAD_POOL_SIZE,
            "maxConcurrentThumbs": MAX_CONCURRENT_THUMBS,
            "maxConcurrentTiles": MAX_CONCURRENT_TILES,
            "tileSize": TILE_SIZE,
            "tileQuality": TILE_QUALITY,
            "thumbMaxPx": THUMB_MAX_PX,
            "jpegOptimize": JPEG_OPTIMIZE,
            "jpegProgressive": JPEG_PROGRESSIVE,
            "thumbPreferAssociated": THUMB_PREFER_ASSOCIATED,
        },
        "registry": registry.stats(),
        "packageRegistry": package_registry.stats(),
        "colorNormalization": _color_norm_stats(),
        "slidePool": slide_pool.stats(),
        "dzPool": dz_pool.stats(),
        "caches": _cache_stats(),
    }


@app.get("/health")
async def health():
    root_ok = ROOT_PATH.exists() and ROOT_PATH.is_dir()
    package_root_ok = PACKAGE_ROOT.exists() and PACKAGE_ROOT.is_dir()
    return {
        "status": "healthy" if (root_ok and package_root_ok) else "degraded",
        "rootOk": root_ok,
        "packageRootOk": package_root_ok,
        "root": str(ROOT_PATH),
        "packageRoot": str(PACKAGE_ROOT),
        "extensions": sorted(list(EXTENSIONS)),
        "scanCacheSeconds": SCAN_CACHE_SECONDS,
        "slidePoolSize": SLIDE_POOL_SIZE,
        "dzPoolSize": DZ_POOL_SIZE,
        "registry": registry.stats(),
        "packageRegistry": package_registry.stats(),
        "colorNormalization": _color_norm_stats(),
        "slidePool": slide_pool.stats(),
        "dzPool": dz_pool.stats(),
        "caches": _cache_stats(),
    }


@app.on_event("startup")
async def _startup():
    log.info("Local WSI viewer startup, root=%s, package_root=%s", ROOT_PATH, PACKAGE_ROOT)
    if not ROOT_PATH.exists():
        log.warning("Configured root path does not exist: %s", ROOT_PATH)
    COLOR_NORM_ROOT.mkdir(parents=True, exist_ok=True)
    await _run_blocking(registry.refresh_if_needed, True, timeout=120.0)
    await _run_blocking(package_registry.refresh, timeout=60.0)


@app.on_event("shutdown")
async def _shutdown():
    log.info("Local WSI viewer shutdown")
    dzi_cache.clear()
    meta_cache.clear()
    thumb_cache.clear()
    tile_cache.clear()
    dz_pool.clear()
    slide_pool.close_all()
    executor.shutdown(wait=False, cancel_futures=True)
