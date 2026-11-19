.PHONY: all-in-one smoke install test lint type cov sandbox-image test-docker dashboards check-monitoring up down
install:        ; pip install -e ".[dev,docker,ui]"
test:           ; pytest -q
lint:           ; ruff check src tests scripts
type:           ; mypy src
cov:            ; pytest --cov=safeloop --cov-report=term-missing -q
sandbox-image:  ; docker build -f Dockerfile.sandbox -t safeloop-sandbox:1 .
test-docker:    sandbox-image ; pytest -m docker -v
dashboards:     ; python scripts/gen_dashboards.py
check-monitoring: ; docker run --rm --entrypoint promtool -v $$PWD/monitoring/prometheus:/p:ro prom/prometheus:v2.55.0 check config /p/prometheus.yml
up:             ; docker compose --profile build build sandbox-image && docker compose up -d --build
down:           ; docker compose down
all-in-one:    ; ./scripts/setup_and_run.sh all
smoke:         ; ./scripts/smoke_stack.sh
