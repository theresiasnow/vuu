#!/usr/bin/env bash
# VUU installer — downloads the latest wheel from GitHub Releases
# and installs it with uv, pipx, or pip (in that order of preference).
set -euo pipefail

REPO="theresiasnow/vuu"
WHEEL_NAME_GLOB="vuu-*-py3-none-any.whl"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Fetching latest VUU release info..."
ASSET_URL=$(
  curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest" \
    | grep -oE '"browser_download_url": *"[^"]+\.whl"' \
    | head -1 \
    | cut -d'"' -f4
)

if [[ -z "${ASSET_URL}" ]]; then
  echo "ERROR: no .whl asset found in latest release of ${REPO}" >&2
  exit 1
fi

WHEEL="${TMPDIR}/$(basename "$ASSET_URL")"
echo "==> Downloading $(basename "$ASSET_URL")"
curl -fsSL "$ASSET_URL" -o "$WHEEL"

if command -v uv >/dev/null 2>&1; then
  echo "==> Installing with uv tool"
  uv tool install --force "$WHEEL"
elif command -v pipx >/dev/null 2>&1; then
  echo "==> Installing with pipx"
  pipx install --force "$WHEEL"
elif command -v pip >/dev/null 2>&1; then
  echo "==> Installing with pip --user"
  pip install --user --force-reinstall "$WHEEL"
else
  echo "ERROR: need uv, pipx, or pip on PATH" >&2
  exit 1
fi

echo
echo "==> Done. Run:  vuu"
