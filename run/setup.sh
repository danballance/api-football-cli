#!/usr/bin/env bash
set -euo pipefail

echo "==> Codex local startup"

cd /home/anoni/Code/python/api-football-cli

echo "==> Checking uv"
command -v uv >/dev/null || {
	echo "uv is required but not installed"
	exit 1
}

echo "==> Syncing Python environment"
uv sync --all-extras --dev

