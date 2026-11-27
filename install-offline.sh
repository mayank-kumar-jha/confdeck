#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${CONFDECK_PYTHON:-python3.11}"
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check --no-index --find-links wheels confdeck
printf '\nInstalled offline. Run ./confdeck.sh doctor, then ./confdeck.sh start.\n'
