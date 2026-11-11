#!/bin/bash
# Exercise the real desktop backend; the headless release check cannot load-test libqxcb.
set -euo pipefail

log=$(mktemp)
"$1" >"$log" 2>&1 &
pid=$!
cleanup() {
    cat "$log"
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    rm -f "$log"
}
trap cleanup EXIT

for attempt in $(seq 1 90); do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo 'AppImage exited before showing its main window' >&2
        exit 1
    fi
    if xdotool search --onlyvisible --name '^FlatCAM Evo [0-9]' >/dev/null 2>&1; then
        sleep 10
        kill -0 "$pid"
        xdotool search --onlyvisible --name '^FlatCAM Evo [0-9]' >/dev/null
        if xdotool search --onlyvisible --name '^Critical Error$' >/dev/null 2>&1; then
            echo 'AppImage opened a critical error dialog' >&2
            exit 1
        fi
        echo 'FLATCAM_APPIMAGE_GUI_OK'
        exit 0
    fi
    sleep 1
done

echo 'AppImage did not show its main window within 90 seconds' >&2
exit 1
