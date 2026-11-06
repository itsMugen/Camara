#!/usr/bin/env bash
#
# The checks, all of them: CI (.github/workflows/ci.yml, on the self-hosted
# runner on the Mac) runs exactly this, and so can you. Needs Docker and uv.
#
#   scripts/ci.sh            # from url-shortener/
#
# 1. lint: ruff check, ruff format --check and mypy, on Python 3.12
# 2. tests: pytest with coverage (95 % floor) on Python 3.11, 3.12 and 3.13,
#    each against a MongoDB 7.0 of its own. A skipped test fails the run: the
#    16 integration tests skip themselves without a database, and here there
#    always is one.
# 3. install: ./install.sh as a user would run it, then a minify/expand round
#    trip through the installed command
#
# Nothing of yours is touched. Each Python gets a venv in a scratch directory
# (uv downloads the interpreter if this machine lacks it). Every MongoDB is a
# throwaway container on a free loopback port, never 27017. install.sh runs
# under a Compose project and a uv tool directory of its own: on a runner the
# checkout is also called url-shortener, so with the defaults it would share
# your own MongoDB container, its port and its mongo-data volume, and replace
# your installed url-shortener. All of it is removed on the way out, also on
# failure or ^C.

set -Eeuo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

command -v uv >/dev/null 2>&1 || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running; MongoDB needs it" >&2; exit 1; }

run_id="url-shortener-ci-$$"
scratch="$(mktemp -d)"
cleanup() {
    # -v: the image declares its data directories volumes.
    docker ps -aq --filter "label=$run_id" | xargs docker rm -f -v >/dev/null 2>&1 || true
    docker compose --project-name "$run_id-install" --file compose.yaml down -v >/dev/null 2>&1 || true
    rm -rf "$scratch"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

free_port() {
    python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

# A venv for one Python, with the package and its dev tools at the pinned versions.
venv() {
    local dir="$scratch/py$1"
    if [[ ! -d "$dir" ]]; then
        uv venv --quiet --python "$1" "$dir" >&2
        uv pip install --quiet --python "$dir/bin/python" -e ".[dev]" -c constraints.txt >&2
    fi
    echo "$dir/bin"
}

# A throwaway MongoDB; prints its URI once it answers.
start_mongo() {
    local name="$run_id-mongo-$1" port
    docker run -d --name "$name" --label "$run_id" -p 127.0.0.1::27017 mongo:7.0.43 >/dev/null
    port="$(docker port "$name" 27017/tcp | head -1 | sed 's/.*://')"
    for _ in $(seq 60); do
        docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1 && break
        sleep 1
    done
    docker exec "$name" mongosh --quiet --eval 'db.adminCommand("ping").ok' >/dev/null 2>&1 \
        || { echo "MongoDB did not come up" >&2; docker logs --tail 20 "$name" >&2; exit 1; }
    echo "mongodb://127.0.0.1:$port"
}

step "lint (Python 3.12)"
bin="$(venv 3.12)"
"$bin/ruff" check .
"$bin/ruff" format --check .
"$bin/mypy"

for py in 3.11 3.12 3.13; do
    step "tests (Python $py)"
    bin="$(venv "$py")"
    uri="$(start_mongo "$py")"
    SHORTENER_TEST_MONGO_URI="$uri" "$bin/pytest" --cov --cov-report=term-missing \
        --cov-fail-under=95 | tee "$scratch/pytest-$py.out"
    if grep -Eq '[0-9]+ skipped' "$scratch/pytest-$py.out"; then
        echo "tests were skipped on Python $py: with a MongoDB here, none should be" >&2
        exit 1
    fi
done

step "install with ./install.sh, then a round trip"
port="$(free_port)"
mkdir -p "$scratch/tool-bin"
COMPOSE_PROJECT_NAME="$run_id-install" MONGO_HOST_PORT="$port" \
    UV_TOOL_DIR="$scratch/tools" UV_TOOL_BIN_DIR="$scratch/tool-bin" \
    PATH="$scratch/tool-bin:$PATH" ./install.sh
url='https://www.example.com/path?q=search'
export SHORTENER_MONGO_URI="mongodb://127.0.0.1:$port"
short="$("$scratch/tool-bin/url-shortener" --minify="$url")"
test "$("$scratch/tool-bin/url-shortener" --expand="$short")" = "$url" \
    || { echo "the installed command did not expand $short back to $url" >&2; exit 1; }
echo "$short -> $url"

step "all green"
