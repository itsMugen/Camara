#!/bin/sh
# Install the url-shortener command and start MongoDB in Docker.
#
#   ./install.sh
#   url-shortener --minify='https://www.example.com/path?q=search'
#
# Does the two Quick start steps of the README:
#
#   1. docker compose up --detach --wait mongo
#      MongoDB on 127.0.0.1:27017, data in the mongo-data volume. It keeps
#      running in the background; stop it with `docker compose down`.
#   2. uv tool install . (or pipx install .)
#      The CLI in its own environment, on your PATH. uv also downloads a
#      suitable Python if this machine has no Python 3.11 or newer.
#
# Safe to run again: MongoDB is left running and the CLI is rebuilt from the
# current source. Dependencies are pinned by constraints.txt, as in CI. Uninstall with `uv tool uninstall url-shortener` (or pipx uninstall).
set -eu

project=$(cd "$(dirname "$0")" && pwd)

fail() {
    echo "error: $1" >&2
    exit 1
}

if ! command -v docker >/dev/null 2>&1; then
    fail "Docker is required to run MongoDB: https://docs.docker.com/get-docker/"
fi
if ! docker info >/dev/null 2>&1; then
    fail "Docker is installed but not running. Start Docker Desktop (or the Docker daemon) and try again."
fi
if command -v uv >/dev/null 2>&1; then
    installer=uv
elif command -v pipx >/dev/null 2>&1; then
    installer=pipx
else
    fail "uv or pipx is required to install the CLI. Install uv (it also provides Python 3.11+ if needed): https://docs.astral.sh/uv/getting-started/installation/"
fi

echo "==> Starting MongoDB in Docker"
docker compose --file "$project/compose.yaml" up --detach --wait mongo

echo "==> Installing the url-shortener command with $installer"
if [ "$installer" = uv ]; then
    uv tool install --reinstall-package url-shortener \
        --constraints "$project/constraints.txt" "$project"
else
    pipx install --force --pip-args="--constraint '$project/constraints.txt'" "$project"
fi

echo
if command -v url-shortener >/dev/null 2>&1; then
    echo "Done. Try: url-shortener --minify='https://www.example.com/path?q=search'"
else
    echo "Installed, but the folder holding url-shortener is not on your PATH yet."
    if [ "$installer" = uv ]; then
        echo "Run 'uv tool update-shell' and open a new terminal."
    else
        echo "Run 'pipx ensurepath' and open a new terminal."
    fi
fi
if [ "${MONGO_HOST_PORT:-27017}" != 27017 ]; then
    echo "MongoDB is on port $MONGO_HOST_PORT: export SHORTENER_MONGO_URI=mongodb://localhost:$MONGO_HOST_PORT"
fi
echo "MongoDB keeps running in the background; stop it with 'docker compose down'."
