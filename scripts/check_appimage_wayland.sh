#!/bin/bash
# Start the AppImage on a Wayland session (weston headless), like a GNOME desktop: the GTK3 platform theme is loaded
# (issue #90, XKB data) and Qt picks the Wayland platform (issue #91). Mesa gives desktop GL here, so the NVIDIA
# OpenGL ES case is not covered.
set -euo pipefail

export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"
log=/results/wayland.log
shot=/results/wayland-canvas.npy
rm -f "$shot"

weston --backend=headless --renderer=pixman --socket=wayland-1 >/tmp/weston.log 2>&1 &
wpid=$!
trap 'kill "$wpid" "${pid:-}" 2>/dev/null || true' EXIT
for attempt in $(seq 1 30); do
    [ -S "$XDG_RUNTIME_DIR/wayland-1" ] && break
    kill -0 "$wpid" || { echo 'weston exited' >&2; cat /tmp/weston.log >&2; exit 1; }
    sleep 1
done
[ -S "$XDG_RUNTIME_DIR/wayland-1" ] || { echo 'weston has no socket' >&2; cat /tmp/weston.log >&2; exit 1; }

unset DISPLAY
export WAYLAND_DISPLAY=wayland-1 QT_QPA_PLATFORM=wayland XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=GNOME
export FLATCAM_CANVAS_PROF=1 FLATCAM_CANVAS_PROF_PAN=1 FLATCAM_CANVAS_PROF_PAN_DELAY=5
export FLATCAM_CANVAS_PROF_SHOT="$shot"
echo 'Starting AppImage on the Wayland session'
"$1" '--command=new_geometry smoke; add_polygon smoke 0 0 10 0 10 10 0 10; plot_all' >"$log" 2>&1 &
pid=$!

fail() {
    echo "$1" >&2
    tail -n 60 "$log" >&2 || true
    exit 1
}

for attempt in $(seq 1 90); do
    kill -0 "$pid" 2>/dev/null || fail 'AppImage exited before showing its canvas'
    grep -q 'Failed to create XKB keymap' "$log" && fail 'XKB keymap failed'
    if [ -s "$shot" ]; then
        sleep 5
        kill -0 "$pid" 2>/dev/null || fail 'AppImage exited after showing its canvas'
        grep -q 'Failed to create XKB keymap' "$log" && fail 'XKB keymap failed'
        grep -q 'OpenGL context: platform wayland' "$log" || fail 'no "OpenGL context: platform wayland" line'
        echo 'FLATCAM_APPIMAGE_WAYLAND_OK'
        exit 0
    fi
    sleep 1
done
fail 'AppImage did not render its canvas within 90 seconds'
