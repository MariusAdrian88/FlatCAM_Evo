#!/bin/bash
# Exercise the real desktop backend; the headless release check cannot load-test libqxcb.
set -euo pipefail

Xvfb "$DISPLAY" -screen 0 1280x1024x24 -nolisten tcp -ac &
xpid=$!
trap 'kill "$xpid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 30); do
    if timeout 5 glxinfo -B; then
        break
    fi
    kill -0 "$xpid"
    sleep 1
done
timeout 5 glxinfo -B
export FLATCAM_CANVAS_PROF=1 FLATCAM_CANVAS_PROF_PAN=1 FLATCAM_CANVAS_PROF_PAN_DELAY=5
export FLATCAM_CANVAS_PROF_SHOT=/results/canvas.npy
echo 'Starting AppImage on the X11 desktop'
"$1" '--command=new_geometry smoke; add_polygon smoke 0 0 10 0 10 10 0 10; plot_all' &
pid=$!
trap 'kill "$pid" "$xpid" 2>/dev/null || true' EXIT

for attempt in $(seq 1 90); do
    if ! kill -0 "$pid" 2>/dev/null; then
        echo 'AppImage exited before showing its main window' >&2
        exit 1
    fi
    if [ -s "$FLATCAM_CANVAS_PROF_SHOT" ] && \
            timeout 5 xdotool search --onlyvisible --name '^FlatCAM Evo [0-9]' >/dev/null 2>&1; then
        sleep 10
        kill -0 "$pid"
        timeout 5 xdotool search --onlyvisible --name '^FlatCAM Evo [0-9]' >/dev/null
        if timeout 5 xdotool search --onlyvisible --name '^Critical Error$' >/dev/null 2>&1; then
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
