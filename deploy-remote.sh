#!/usr/bin/env bash
# Runs ON YOUR LAPTOP: pull the latest pushed code on the VM and run deploy.sh there.
# Requires the "boogle-vm" host in ~/.ssh/config.
set -euo pipefail
cd "$(dirname "$0")"

HOST=boogle-vm
PUBLIC_URL=http://35.252.229.92

# The VM deploys what's on GitHub, so refuse if local work isn't pushed yet.
if [[ -n "$(git status --porcelain)" ]]; then
    echo "!! You have uncommitted changes. Commit and push first."
    exit 1
fi
git fetch -q origin
if [[ "$(git rev-parse HEAD)" != "$(git rev-parse @{u})" ]]; then
    echo "!! Local branch differs from origin. Push (or pull) first."
    exit 1
fi

# The model is too big for git, so it goes straight to the VM, and only when it changed.
if [[ "$(shasum -a 256 < model.npz | cut -d' ' -f1)" != "$(ssh "$HOST" 'sha256sum < ~/boogle/model.npz 2>/dev/null | cut -d" " -f1')" ]]; then
    echo "==> Uploading model.npz"
    scp -q model.npz "$HOST:boogle/model.npz.tmp"
    ssh "$HOST" 'mv ~/boogle/model.npz.tmp ~/boogle/model.npz'
fi

echo "==> Deploying $(git log -1 --format='%h %s') to $HOST"
ssh "$HOST" 'cd ~/boogle && git pull --ff-only -q && ./deploy.sh'

echo "==> Checking from the public internet"
curl -fsS "$PUBLIC_URL/jsontest"
echo
