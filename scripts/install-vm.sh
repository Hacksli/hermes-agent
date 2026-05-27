#!/bin/sh
# install-vm.sh — Install Hermes Agent on an Alpine Linux VM (youself.io gold image)
#
# Usage:
#   sh install-vm.sh
#
# The script:
#   1. Installs Hermes from the GitHub repository.
#   2. Creates required runtime directories.
#   3. Installs the OpenRC service file (if /etc/init.d is present).
#
# Environment variable overrides:
#   HERMES_REPO   — git URL to install from (default: GitHub Hacksli fork)
#   HERMES_BRANCH — branch/tag to install (default: HEAD)

set -eu

HERMES_REPO="${HERMES_REPO:-git+https://github.com/Hacksli/hermes-agent}"

echo "[install-vm] Installing Hermes Agent from ${HERMES_REPO} ..."
pip3 install --no-cache-dir "${HERMES_REPO}"

echo "[install-vm] Creating runtime directories ..."
mkdir -p /var/log/hermes /etc/hermes

# Install OpenRC service if running on a system with /etc/init.d (Alpine/OpenRC)
INIT_D="/etc/init.d"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_SRC="${SCRIPT_DIR}/../etc/init.d/hermes"

if [ -d "${INIT_D}" ] && [ -f "${SERVICE_SRC}" ]; then
    echo "[install-vm] Installing OpenRC service to ${INIT_D}/hermes ..."
    cp "${SERVICE_SRC}" "${INIT_D}/hermes"
    chmod 755 "${INIT_D}/hermes"
    echo "[install-vm] Enable with: rc-update add hermes default && rc-service hermes start"
else
    echo "[install-vm] Skipping OpenRC service install (${INIT_D} not found or service file missing)."
fi

echo "[install-vm] Done. Set YOUSELF_GATEWAY_URL and YOUSELF_GATEWAY_TOKEN in /etc/hermes/env"
echo "[install-vm] then start with: python3 -m hermes --transport youself_gateway"
