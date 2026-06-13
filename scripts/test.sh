#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -d .venv ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
.venv/bin/pip install -q -r requirements.txt -r requirements-dev.txt

export MPLBACKEND=Agg
export CI_TEST_REPORT=ci-test-report.md
.venv/bin/pytest tests/ -v --tb=short --junitxml=junit.xml "$@"

if [ -f ci-test-report.md ]; then
  echo ""
  echo "Detailed report: ci-test-report.md"
fi
