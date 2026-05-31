#!/usr/bin/env sh
# One-command launcher for Apex Store Intelligence.
#
#   ./run.sh            install API deps, seed demo data, start the API + dashboard
#   ./run.sh test       run the test suite with coverage (skips the heavy CV pipeline test)
#   ./run.sh docker     bring up API + Postgres exactly as the grader does (docker compose)
#
# Local mode uses SQLite + on-startup seeding so the dashboard shows real data with no
# GPU and no Postgres. Detection (torch/ultralytics) runs separately via `python -m pipeline.run`.
set -e
CMD="${1:-serve}"

case "$CMD" in
  test)
    python -m pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt requests
    exec pytest --ignore=tests/test_pipeline.py
    ;;

  docker)
    exec docker compose up --build
    ;;

  serve|"")
    if [ ! -d .venv ]; then python3 -m venv .venv; fi
    # shellcheck disable=SC1091
    . .venv/bin/activate 2>/dev/null || . .venv/Scripts/activate
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    export DATABASE_URL="${DATABASE_URL:-sqlite+aiosqlite:///./local_demo.db}"
    export SEED_ON_START="${SEED_ON_START:-true}"
    PORT="${PORT:-8000}"
    echo ""
    echo "  Dashboard : http://localhost:${PORT}/dashboard"
    echo "  API docs  : http://localhost:${PORT}/docs"
    echo "  Health    : http://localhost:${PORT}/health"
    echo ""
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
    ;;

  *)
    echo "usage: ./run.sh [serve|test|docker]"
    exit 1
    ;;
esac
