#!/bin/bash
# Runs within the existing mini-PC X11 session. Does not change network settings.
set -u
export DISPLAY="${DISPLAY:-:0}"
TASK_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
children=()
cleanup() {
    for child in "${children[@]}"; do kill "$child" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'exit 0' TERM INT
if command -v xset >/dev/null; then
    xset s off 2>/dev/null || true
    xset s noblank 2>/dev/null || true
    xset -dpms 2>/dev/null || true
fi
if command -v unclutter >/dev/null; then
    unclutter -idle 0.1 -root &
    children+=("$!")
fi
if command -v openbox >/dev/null && ! pgrep -u "$(id -u)" -x openbox >/dev/null; then
    openbox &
    children+=("$!")
fi
python3 "$TASK_DIR/kiosk_watch.py" &
children+=("$!")
wait "${children[-1]}"
