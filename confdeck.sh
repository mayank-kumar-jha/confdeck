#!/bin/sh
set -eu
CONFDECK_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export CONFDECK_BIN="$CONFDECK_ROOT/vendor/bin"
exec "$CONFDECK_ROOT/.venv/bin/python" -m confdeck "$@"
