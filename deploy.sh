#!/usr/bin/env bash
# Runs ON THE VM: install dependencies, restart the service, check it's serving.
# Normally invoked by deploy-remote.sh from your laptop, after a git pull.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing dependencies"
.venv/bin/pip install -q -r requirements.txt

echo "==> Restarting boogle service"
sudo systemctl restart boogle
sleep 2

if ! systemctl is-active --quiet boogle; then
    echo "!! boogle failed to start. Recent logs:"
    journalctl -u boogle -n 30 --no-pager
    exit 1
fi

echo "==> Health check"
curl -fsS http://localhost/jsontest
echo
echo "==> Deployed $(git log -1 --format='%h %s')"
