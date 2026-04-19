from __future__ import annotations

import argparse
import os
import socket
import sys
from pathlib import Path

import uvicorn


DEFAULT_EXTENSIONS = ".svs,.tif,.tiff,.ndpi,.mrxs,.scn,.bif,.vms,.vmu,.svslide,.jpg,.jpeg,.png,.bmp"


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
            return False
        except OSError:
            return True


def _pick_port(host: str, port: int, auto_port: bool) -> int:
    if not _port_in_use(host, port):
        return port
    if not auto_port:
        raise RuntimeError(
            f"Port {port} is already in use. Use --port <new_port> or add --auto-port."
        )
    for candidate in range(port + 1, port + 101):
        if not _port_in_use(host, candidate):
            print(f"Port {port} is in use, switched to {candidate}.")
            return candidate
    raise RuntimeError(
        f"Port {port} is in use and no free port was found in [{port + 1}, {port + 100}]."
    )


def _resolve_directory(path_value: str, label: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise RuntimeError(f"{label} does not exist: {path}")
    return path


def _configure_environment(args: argparse.Namespace) -> tuple[Path, Path | None]:
    slide_root = _resolve_directory(args.slide_root, "SlideRoot")

    os.environ["WSI_LOCAL_ROOT"] = str(slide_root)
    os.environ["WSI_LOCAL_EXTENSIONS"] = args.extensions
    os.environ["WSI_LOCAL_SCAN_CACHE_SECONDS"] = str(args.scan_cache_seconds)

    package_root: Path | None = None
    if args.package_root:
        package_root = Path(args.package_root).expanduser().resolve()
        package_root.mkdir(parents=True, exist_ok=True)
        os.environ["WSI_LOCAL_PACKAGE_ROOT"] = str(package_root)
    else:
        os.environ.pop("WSI_LOCAL_PACKAGE_ROOT", None)

    return slide_root, package_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wsi-local-viewer",
        description="Run Local WSI Viewer with optional package receiver and online GDDN panel.",
    )
    parser.add_argument("--slide-root", required=True, help="Root directory containing WSI files.")
    parser.add_argument("--port", type=int, default=8011, help="Bind port (default: 8011).")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1).")
    parser.add_argument(
        "--package-root",
        default="",
        help="Optional package storage directory for incoming .tawpkg/.zip bundles.",
    )
    parser.add_argument(
        "--extensions",
        default=DEFAULT_EXTENSIONS,
        help="Comma-separated allowed file extensions.",
    )
    parser.add_argument(
        "--scan-cache-seconds",
        type=int,
        default=15,
        help="Slide filesystem index cache lifetime in seconds.",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable uvicorn reload mode (development only).",
    )
    parser.add_argument(
        "--auto-port",
        action="store_true",
        help="Auto-pick the next free port when --port is occupied.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        slide_root, package_root = _configure_environment(args)
        selected_port = _pick_port(args.host, args.port, args.auto_port)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Starting local WSI viewer...")
    print(f"Root: {slide_root}")
    if package_root:
        print(f"PackageRoot: {package_root}")
    print(f"URL : http://{args.host}:{selected_port}")

    uvicorn.run(
        "app.local_main:app",
        host=args.host,
        port=selected_port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
