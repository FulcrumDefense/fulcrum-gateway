#!/usr/bin/env bash
# Stops the gateway, rotates logs, upgrades axctl from the local editable
# install, and restarts with request logging enabled.
#
# Dev workflow: bump the version in pyproject.toml before running this script
# so the installed metadata reflects the current build. The convention in this
# repo is to increment the dev version (e.g. 0.6.0.dev12 -> 0.6.0.dev13)
# after each code change so it's always clear which build is running.
set -euo pipefail

echo "==> Stopping gateway..."
ax gateway stop || true

echo "==> Rotating logs..."
LOG_DIR="${HOME}/.ax/gateway"
for log in api-requests.log gateway.log gateway-ui.log; do
    if [ -f "${LOG_DIR}/${log}" ]; then
        mv "${LOG_DIR}/${log}" "${LOG_DIR}/${log}.$(date +%Y%m%d-%H%M%S)"
    fi
done

echo "==> Upgrading axctl..."
# Assumes axctl was installed via 'pipx install -e .' from this checkout.
# pipx records the original spec, so upgrade re-installs from the local path.
# If it was installed from PyPI instead, this will pull the published package.
pipx upgrade axctl

echo "==> Starting gateway..."
AX_LOG_API_REQUESTS=1 ax gateway start --no-open

echo "==> Done. $(ax --version)"
