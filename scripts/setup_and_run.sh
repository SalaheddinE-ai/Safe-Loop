#!/usr/bin/env bash
# One-shot setup. Usage: ./scripts/setup_and_run.sh [test|docker|up|all]
#   test   : venv + install + lint + types + unit/integration/e2e tests   (no Docker needed)
#   docker : build sandbox image + run REAL isolation tests                (needs Docker)
#   up     : create .env if missing, build and start the Compose stack     (needs Docker, Ollama optional)
#   all    : test + docker + up
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-all}"
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

do_test() {
  say "Python environment"
  [ -d .venv ] || python3 -m venv .venv
  # shellcheck disable=SC1091
  source .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -e ".[dev,docker,ui,otel]"
  say "Lint, types, tests"
  ruff check src tests scripts
  mypy src
  pytest -q
}

do_docker() {
  command -v docker >/dev/null || { echo "docker not found"; exit 2; }
  docker info >/dev/null 2>&1 || { echo "docker daemon not reachable"; exit 2; }
  # shellcheck disable=SC1091
  source .venv/bin/activate
  say "Build sandbox image"
  docker build -f Dockerfile.sandbox -t safeloop-sandbox:1 .
  say "REAL isolation tests (network, read-only FS, timeout, memory, secrets, cleanup)"
  pytest -m docker -v
}

do_up() {
  command -v docker >/dev/null || { echo "docker not found"; exit 2; }
  if [ ! -f .env ]; then
    say "Creating .env with random secrets"
    token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
    gpw="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
    gid="$(getent group docker 2>/dev/null | cut -d: -f3 || true)"
    cp .env.example .env
    sed -i.bak "s|^SAFELOOP_API_TOKEN=.*|SAFELOOP_API_TOKEN=${token}|; s|^GRAFANA_ADMIN_PASSWORD=.*|GRAFANA_ADMIN_PASSWORD=${gpw}|; s|^DOCKER_GID=.*|DOCKER_GID=${gid:-999}|" .env
    rm -f .env.bak
    echo "Secrets written to .env (do not commit it)."
  fi
  say "Build sandbox image + start stack"
  docker compose --profile build build sandbox-image
  docker compose up -d --build
  say "Waiting for API"
  for _ in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/metrics >/dev/null 2>&1; then break; fi
    sleep 2
  done
  curl -fsS http://127.0.0.1:8000/metrics >/dev/null || { docker compose logs api | tail -30; exit 1; }
  cat <<MSG

Stack is up (all ports bound to 127.0.0.1):
  UI          http://127.0.0.1:8501
  API docs    http://127.0.0.1:8000/docs
  Prometheus  http://127.0.0.1:9090
  Grafana     http://127.0.0.1:3000   (user: admin, password in .env)
Next:  ./scripts/smoke_stack.sh     (submits a real task through the API)
MSG
}

case "$MODE" in
  test) do_test ;;
  docker) do_docker ;;
  up) do_up ;;
  all) do_test; do_docker; do_up ;;
  *) echo "usage: $0 [test|docker|up|all]"; exit 2 ;;
esac
