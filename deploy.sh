#!/usr/bin/env bash
# Runs ON THE VM: install dependencies, restart the service, check it's serving.
# Normally invoked by deploy-remote.sh from your laptop, after a git pull.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing dependencies"
.venv/bin/pip install -q -r requirements.txt

# Fails here, before the restart, if the database isn't set up: the old code keeps serving.
echo "==> Applying database schema"
PGOPTIONS='-c client_min_messages=warning' psql -q -v ON_ERROR_STOP=1 -d boogle -f schema_handout.sql

# Fails here too, before the restart, if the code or model can't load: same reason.
echo "==> Checking the app loads"
.venv/bin/python -c 'import app'

echo "==> Restarting boogle service"
sudo systemctl restart boogle
sleep 2

if ! systemctl is-active --quiet boogle; then
    echo "!! boogle failed to start. Recent logs:"
    journalctl -u boogle -n 30 --no-pager
    exit 1
fi

echo "==> Health check"
# gunicorn only opens its ports once the model has loaded, so allow it a few seconds.
curl -fsS --retry 15 --retry-connrefused --retry-delay 1 http://localhost/jsontest
echo
curl -fsS http://localhost/api/history   # goes through the database
echo
echo "==> Deployed $(git log -1 --format='%h %s')"
