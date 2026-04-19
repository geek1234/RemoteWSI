#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SLIDE_ROOT=""
PACKAGE_ROOT="${HOME}/.wsi_local_viewer/packages"
HOST="0.0.0.0"
PORT="8011"
PYTHON_BIN="python3"
VENV_DIR="${REPO_ROOT}/.venv-remote"
INSTALL_DEPS="1"

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/run-remote-viewer.sh --slide-root <path> [options]

Required:
  --slide-root <path>        Linux server path containing SVS/WSI files

Options:
  --package-root <path>      Package storage root (default: ~/.wsi_local_viewer/packages)
  --host <host>              Bind host (default: 0.0.0.0)
  --port <port>              Bind port (default: 8011)
  --python-bin <bin>         Python executable (default: python3)
  --venv-dir <path>          Virtualenv dir (default: <repo>/.venv-remote)
  --skip-install             Skip pip dependency install
  -h, --help                 Show help

Example:
  ./scripts/run-remote-viewer.sh \
    --slide-root /data3/Eryuan/Transfer/HCCdata/HCC-pic \
    --package-root /data3/Eryuan/Transfer/HCCdata/HCC-packages \
    --host 0.0.0.0 \
    --port 8011
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slide-root)
      SLIDE_ROOT="${2:-}"
      shift 2
      ;;
    --package-root)
      PACKAGE_ROOT="${2:-}"
      shift 2
      ;;
    --host)
      HOST="${2:-}"
      shift 2
      ;;
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --python-bin)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --venv-dir)
      VENV_DIR="${2:-}"
      shift 2
      ;;
    --skip-install)
      INSTALL_DEPS="0"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[error] Unknown arg: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "${SLIDE_ROOT}" ]]; then
  echo "[error] --slide-root is required." >&2
  usage
  exit 1
fi

if [[ ! -d "${SLIDE_ROOT}" ]]; then
  echo "[error] Slide root does not exist: ${SLIDE_ROOT}" >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "[error] Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if ! command -v ldconfig >/dev/null 2>&1 || ! ldconfig -p 2>/dev/null | grep -qi openslide; then
  echo "[warn] libopenslide is not detected. Install it first (Ubuntu):" >&2
  echo "       sudo apt-get update && sudo apt-get install -y libopenslide0" >&2
fi

cd "${REPO_ROOT}"

if [[ ! -d "${VENV_DIR}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

if [[ "${INSTALL_DEPS}" == "1" ]]; then
  python -m pip install --upgrade pip wheel setuptools
  python -m pip install fastapi "uvicorn[standard]" pillow openslide-python jinja2 python-multipart numpy scipy opencv-python-headless
fi

mkdir -p "${PACKAGE_ROOT}"

export WSI_LOCAL_ROOT="${SLIDE_ROOT}"
export WSI_LOCAL_PACKAGE_ROOT="${PACKAGE_ROOT}"

echo "[info] Starting remote WSI viewer..."
echo "[info] Root: ${WSI_LOCAL_ROOT}"
echo "[info] PackageRoot: ${WSI_LOCAL_PACKAGE_ROOT}"
echo "[info] URL: http://${HOST}:${PORT}"

exec python -m uvicorn app.local_main:app --host "${HOST}" --port "${PORT}"
